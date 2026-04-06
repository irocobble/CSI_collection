import os
import re
import threading
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import serial
from dotenv import load_dotenv

from python.uploader import call_model_api, save_prediction_row, upload_image


load_dotenv()


# ===== SETTINGS =====

ESP_PORTS = os.getenv("ESP_PORTS", "COM6,COM13").split(",")
ESP_PORTS = [port.strip() for port in ESP_PORTS if port.strip()]
BAUD_RATE = int(os.getenv("BAUD_RATE", "115200"))

SUBCARRIERS = int(os.getenv("SUBCARRIERS", "52"))
SAMPLES = int(os.getenv("SAMPLES", "400"))

BASE_OUTPUT_FOLDER = os.getenv("BASE_OUTPUT_FOLDER", r"C:\Users\majum\Downloads\ESP_pro\output")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
BUCKET_NAME = os.getenv("BUCKET_NAME", "csi-images").strip()
TABLE_NAME = os.getenv("TABLE_NAME", "csi_predictions").strip()
ACTIVITY_API_URL = os.getenv("ACTIVITY_API_URL", "").strip()
PRESENCE_API_URL = os.getenv("PRESENCE_API_URL", "").strip()
# ====================


if not ACTIVITY_API_URL or not PRESENCE_API_URL:
    raise RuntimeError("Set ACTIVITY_API_URL and PRESENCE_API_URL in .env")


def log_step(com_port: str, message: str) -> None:
    print(f"[{com_port}] {message}")


def collect_csi(com_port: str) -> None:
    output_folder = os.path.join(BASE_OUTPUT_FOLDER, com_port)

    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    log_step(com_port, "Opening serial connection...")

    ser = serial.Serial(com_port, BAUD_RATE, timeout=1)

    frame_count = 0
    image_count = 0
    total_packets = 0

    log_step(com_port, "Streaming CSI data... Press CTRL+C to stop.")

    while True:
        csi_frames = []

        while len(csi_frames) < SAMPLES:
            try:
                line = ser.readline().decode(errors="ignore").strip()

                if line.startswith("CSI:"):
                    continue

                numbers = re.findall(r"-?\d+", line)

                if len(numbers) >= 128:
                    values = list(map(int, numbers[:128]))

                    amplitudes = []
                    for i in range(0, 128, 2):
                        i_value = values[i]
                        q_value = values[i + 1]
                        amplitude = np.sqrt(i_value**2 + q_value**2)
                        amplitudes.append(amplitude)

                    valid_bins = amplitudes[6:32] + amplitudes[33:59]
                    if len(valid_bins) != SUBCARRIERS:
                        continue

                    csi_frames.append(valid_bins)
                    frame_count += 1
                    total_packets += 1

                    print(
                        f"\r[{com_port}] Packets: {frame_count}/{SAMPLES} | Total: {total_packets}",
                        end="",
                    )
            except Exception:
                pass

        csi_matrix = np.array(csi_frames).T

        matrix_min = np.min(csi_matrix)
        matrix_max = np.max(csi_matrix)
        if matrix_max > matrix_min:
            csi_matrix = (csi_matrix - matrix_min) / (matrix_max - matrix_min)

        filename = f"{com_port}_frame_{image_count}.png"
        filepath = os.path.join(output_folder, filename)

        plt.imsave(filepath, csi_matrix, cmap="gray", origin="lower")

        image_path = Path(filepath)
        print()
        log_step(com_port, f"New image created: {filepath}")
        log_step(com_port, "Uploading image to Supabase...")

        object_path = upload_image(image_path)
        log_step(com_port, f"Uploaded: {object_path}")

        log_step(com_port, "Sending image to activity model...")
        activity_result = call_model_api(ACTIVITY_API_URL, image_path)
        log_step(com_port, "Sending image to presence model...")
        presence_result = call_model_api(PRESENCE_API_URL, image_path)

        save_prediction_row(object_path, activity_result, presence_result)

        log_step(com_port, "Saved prediction row in Supabase")
        log_step(com_port, f"Frame {image_count} complete")

        image_count += 1
        frame_count = 0


# ===== START THREADS =====

threads = []

for port in ESP_PORTS:
    thread = threading.Thread(target=collect_csi, args=(port,))
    thread.start()
    threads.append(thread)


for thread in threads:
    thread.join()
