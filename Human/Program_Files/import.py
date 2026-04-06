import os
import threading
import time
import struct
import cv2
import numpy as np
import serial
import serial.tools.list_ports
import requests
import logging

from pathlib import Path
from supabase import create_client
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

# Global thread pool for offloading network API calls so serial reader isn't blocked 
api_executor = ThreadPoolExecutor(max_workers=8)


# ================= LOGGING =================

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)

def get_logger(port):
    return logging.getLogger(port)


# ================= LOAD ENV =================

load_dotenv()


# ================= SETTINGS =================

BAUD_RATE       = 921600
SUBCARRIERS     = 52
SAMPLES         = 400
FRAME_SIZE      = SUBCARRIERS * SAMPLES

# Frame layout from ESP32:
# b"START"[LEN_HI][LEN_LO][...payload...][CRC_HI][CRC_LO]b"END"
FRAME_START     = b"START"
FRAME_END       = b"END"
HEADER_SIZE     = len(FRAME_START) + 2       # start marker + 2-byte length
FOOTER_SIZE     = 2 + len(FRAME_END)         # 2-byte CRC + end marker
FULL_FRAME_SIZE = HEADER_SIZE + FRAME_SIZE + FOOTER_SIZE

MAX_SYNC_BYTES  = 16384   # bail out if we can't find a start marker in this many bytes
SERIAL_TIMEOUT  = 2.0     # seconds

BASE_OUTPUT_FOLDER = r"C:\Users\majum\Downloads\ESP_pro\output"

SUPABASE_URL      = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
BUCKET_NAME       = os.getenv("BUCKET_NAME", "csi-images").strip()
TABLE_NAME        = os.getenv("TABLE_NAME", "csi_predictions").strip()
ACTIVITY_API_URL  = os.getenv("ACTIVITY_API_URL", "").strip()
PRESENCE_API_URL  = os.getenv("PRESENCE_API_URL", "").strip()

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("Missing Supabase credentials in .env")
if not ACTIVITY_API_URL or not PRESENCE_API_URL:
    raise RuntimeError("Missing API URLs in .env")

supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


# ================= CRC-16/CCITT =================

def crc16(data: bytes) -> int:
    """CRC-16/CCITT — must match the ESP32 firmware implementation."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
        crc &= 0xFFFF
    return crc


# ================= HELPERS =================

def upload_image(image_path: Path) -> str:
    object_name = f"csi/{image_path.name}"
    with image_path.open("rb") as f:
        supabase.storage.from_(BUCKET_NAME).upload(
            path=object_name,
            file=f,
            file_options={"content-type": "image/png", "upsert": "true"}
        )
    return object_name


def call_model_api(api_url: str, image_path: Path) -> dict:
    with image_path.open("rb") as f:
        response = requests.post(api_url, files={"file": f}, timeout=30)
    response.raise_for_status()
    return response.json()


def save_prediction_row(path: str, activity: dict, presence: dict):
    row = dict(
        image_path=path,
        activity_result=activity,
        presence_result=presence
    )
    supabase.table(TABLE_NAME).insert(row).execute()

def background_upload_and_infer(filepath: Path, image_count: int, log: logging.Logger):
    """Executes network calls in a separate thread to keep the serial monitor fast."""
    try:
        object_path     = upload_image(filepath)
        activity_result = call_model_api(ACTIVITY_API_URL, filepath)
        presence_result = call_model_api(PRESENCE_API_URL, filepath)
        save_prediction_row(object_path, activity_result, presence_result)
        log.info(f"Frame {image_count} uploaded and predicted successfully")
    except requests.RequestException as e:
        log.warning(f"API Model call failed for frame {image_count}: {e} — continuing")
    except Exception as e:
        log.error(f"Supabase Upload or Database error for frame {image_count}! Error details: {repr(e)}")


# ================= FRAME READER =================

class FrameReader:
    """
    Stateful frame reader with:
    - CRC-16 validation
    - Fast chunked buffering for b"START" synchronization
    - Integrated text log extraction
    """

    def __init__(self, ser: serial.Serial, logger: logging.Logger):
        self.ser    = ser
        self.log    = logger
        self._buffer = bytearray()

    def _print_logs(self, data: bytes):
        if not data:
            return
        try:
            text = data.decode("ascii", errors="ignore")
            lines = text.split("\n")
            for line in lines:
                line = line.strip()
                if line:
                    self.log.info(f"[ESP32] {line}")
        except Exception:
            pass

    def _sync_to_start(self) -> bool:
        """Scan forward until we find b'START' using fast chunk matching."""
        scanned = 0
        while scanned < MAX_SYNC_BYTES:
            in_waiting = max(self.ser.in_waiting, 4096)
            chunk = self.ser.read(in_waiting)
            if not chunk:
                return False
                
            self._buffer.extend(chunk)
            
            idx = self._buffer.find(FRAME_START)
            if idx != -1:
                # Found the start marker! Evict everything before as logs
                skipped_bytes = self._buffer[:idx]
                self._print_logs(skipped_bytes)
                
                # Keep START and following bytes in buffer
                self._buffer = self._buffer[idx:]
                return True
            else:
                # No START found. Retain enough tail bytes to catch split markers
                safe_len = max(0, len(self._buffer) - (len(FRAME_START) - 1))
                if safe_len > 0:
                    self._print_logs(self._buffer[:safe_len])
                    self._buffer = self._buffer[safe_len:]
                scanned += len(chunk)
                
        self.log.warning(f"Could not find frame start in {MAX_SYNC_BYTES} bytes — port dead?")
        return False

    def _read_exact(self, count: int) -> bytes | None:
        """Helper to read exactly `count` bytes from buffer/serial."""
        while len(self._buffer) < count:
            chunk = self.ser.read(max(count - len(self._buffer), 4096))
            if not chunk:
                return None
            self._buffer.extend(chunk)
        data = self._buffer[:count]
        self._buffer = self._buffer[count:]
        return data

    def read_frame(self) -> bytes | None:
        """
        Read one complete, CRC-validated frame payload.
        Returns raw payload bytes, or None on error.
        """
        if not self._sync_to_start():
            return None

        header_data = self._read_exact(HEADER_SIZE)
        if not header_data:
            self.log.warning("Timeout reading header — resyncing")
            return None
            
        len_bytes = header_data[len(FRAME_START):]
        declared_len = struct.unpack(">H", len_bytes)[0]

        if declared_len != FRAME_SIZE:
            self.log.warning(f"Unexpected frame length {declared_len} (expected {FRAME_SIZE}) — resyncing")
            return None

        tail = self._read_exact(declared_len + FOOTER_SIZE)
        if not tail:
            self.log.warning(f"Incomplete frame — resyncing")
            return None

        payload   = tail[:declared_len]
        crc_recv  = struct.unpack(">H", tail[declared_len:declared_len + 2])[0]
        end_bytes = tail[-len(FRAME_END):]

        if end_bytes != FRAME_END:
            self.log.warning(f"Bad end marker {end_bytes} — resyncing")
            return None

        crc_calc = crc16(payload)
        if crc_calc != crc_recv:
            self.log.warning(f"CRC mismatch: calculated 0x{crc_calc:04X}, received 0x{crc_recv:04X} — dropping frame")
            return None

        return payload


# ================= AUTO PORT DETECT =================

def find_active_ports() -> list[str]:
    """
    Scan all COM ports concurrently and return those with data flowing.
    We wait up to 4.5 seconds to ensure we catch frames that take longer to accumulate.
    """
    ports = [p.device for p in serial.tools.list_ports.comports()]
    if not ports:
        return []
        
    logging.info(f"Scanning available ports for ESP32s: {', '.join(ports)} (this takes ~5 seconds)...")
    
    active_ports = []
    
    def check_port(device):
        try:
            # Short timeout, we'll wait with sleep instead
            ser = serial.Serial(device, BAUD_RATE, timeout=0.1)
            time.sleep(4.5)  # wait long enough for an ESP to transmit it's 4-second aggregated frame
            waiting = ser.in_waiting
            ser.close()
            if waiting > 0:
                logging.info(f"Active ESP32 found on port: {device} ({waiting} bytes waiting)")
                return device
        except Exception as e:
            logging.debug(f"Port {device} skipped: {e}")
        return None

    # Check all ports at the same time so startup isn't painfully slow
    with ThreadPoolExecutor(max_workers=max(1, len(ports))) as executor:
        results = executor.map(check_port, ports)
        for res in results:
            if res:
                active_ports.append(res)
                
    return active_ports


# ================= CSI LISTENER =================

# Global state to serialize filenames consistently across any number of streaming ESP32 boards
global_image_counter = 1
image_counter_lock = threading.Lock()

def collect_csi(com_port: str):
    log = get_logger(com_port)

    output_folder = Path(BASE_OUTPUT_FOLDER)
    output_folder.mkdir(parents=True, exist_ok=True)

    log.info("Opening serial connection...")

    # --- Open serial port with retry ---
    ser = None
    for attempt in range(5):
        try:
            ser = serial.Serial(
                com_port,
                BAUD_RATE,
                timeout=SERIAL_TIMEOUT
            )
            ser.set_buffer_size(rx_size=131072)  # 128 KB — handles 2+ frames in buffer
            time.sleep(2)
            ser.reset_input_buffer()
            log.info("Serial port opened")
            break
        except Exception as e:
            log.warning(f"Open attempt {attempt + 1}/5 failed: {e}")
            time.sleep(2 ** attempt)  # backoff: 1s, 2s, 4s, 8s, 16s

    if ser is None or not ser.is_open:
        log.error("Could not open serial port after 5 attempts — thread exiting")
        return

    reader      = FrameReader(ser, log)
    error_count = 0
    MAX_CONSECUTIVE_ERRORS = 10

    log.info("Listening for CSI frames...")

    while True:
        try:
            payload = reader.read_frame()

            if payload is None:
                error_count += 1
                if error_count >= MAX_CONSECUTIVE_ERRORS:
                    log.error(f"{MAX_CONSECUTIVE_ERRORS} consecutive errors — reopening port")
                    ser.close()
                    time.sleep(3)
                    ser.open()
                    ser.reset_input_buffer()
                    error_count = 0
                continue

            error_count = 0  # reset on success

            # --- Build CSI matrix ---
            frame_array = np.frombuffer(payload, dtype=np.uint8)
            csi_matrix  = frame_array.reshape((SAMPLES, SUBCARRIERS)).T  # shape: (52, 400)

            # Normalize to 0-255 for PNG encoding
            csi_norm = cv2.normalize(csi_matrix, None, 0, 255, cv2.NORM_MINMAX)
            csi_img  = np.uint8(csi_norm)

            # --- Save image ---
            # Extract a unified global ID safely across threads
            global global_image_counter
            with image_counter_lock:
                current_id = global_image_counter
                global_image_counter += 1

            filename = f"{current_id}.png"
            filepath = output_folder / filename
            cv2.imwrite(str(filepath), csi_img)
            log.info(f"Saved {filename}")

            # --- Upload & infer in Background ---
            # Dispatch to ThreadPool instead of blocking the reader loop
            api_executor.submit(background_upload_and_infer, filepath, current_id, log)

        except serial.SerialException as e:
            log.error(f"Serial exception: {e} — attempting reconnect")
            time.sleep(3)
            try:
                ser.close()
                ser.open()
                ser.reset_input_buffer()
                log.info("Reconnected")
            except Exception as reconnect_err:
                log.error(f"Reconnect failed: {reconnect_err}")
                time.sleep(5)

        except Exception as e:
            log.error(f"Unexpected error: {e}")
            time.sleep(0.5)


# ================= ENTRY POINT =================

if __name__ == "__main__":

    ESP_PORTS = find_active_ports()

    if not ESP_PORTS:
        logging.warning("No active ESP32 ports detected. Check connections and baud rate.")
        logging.warning("You can also set ports manually: ESP_PORTS = ['COM3', 'COM4']")
        # Uncomment and edit to force ports:
        # ESP_PORTS = ["COM3", "COM4"]

    logging.info(f"Starting listeners on: {ESP_PORTS}")

    threads = []
    for port in ESP_PORTS:
        t = threading.Thread(
            target=collect_csi,
            args=(port,),
            daemon=True,
            name=f"csi-{port}"
        )
        t.start()
        threads.append(t)

    # Keep main thread alive; Ctrl+C exits cleanly
    try:
        while True:
            alive = [t for t in threads if t.is_alive()]
            if not alive:
                logging.error("All listener threads have died — exiting")
                break
            time.sleep(5)
    except KeyboardInterrupt:
        logging.info("Interrupted by user — shutting down")