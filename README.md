# Human Presence Detection using Wi-Fi CSI

**Team Name:** ?

## AIM
To develop a prototype device that can sense human presence in a site of interest without using cameras, wearables, or expensive radar hardware. This is achieved by analyzing how Wi-Fi signals change when a person is present.

---

## Research Path — What We Tried First

To achieve human presence detection, our team initially explored IoT devices to detect presence.

1.  **DFRobot C4001 (24 GHz FMCW radar):** This uses a remarkably high frequency of 24 GHz to sense human presence. It works by sending an electromagnetic wave at 24 GHz, and when it reflects back and is captured by the sensor, the wave gets induced by a phase difference. We thought this small phase difference would be enough to accurately detect a human, but these devices require an extremely clean environment to function properly.
2.  **Secondary FMCW IoT Sensor:** We tried a similar IoT device, but it was equally ineffective. Both devices operated on **FMCW (Frequency Modulated Continuous Wave)**. We advise avoiding this approach as it becomes very complicated, although ECE students interested in this can explore it further.

**The Breakthrough:** After failing to extract useful information from these radar devices, we dove deeper and found our solution: **CSI data**.

---

## How It Works — The Big Picture

The system follows a specific pipeline:

**ESP32-S3** → **CSI matrix** → **Spectrogram image** → **EfficientNetV2** → **Activity label** → **Dashboard**

A Wi-Fi router sends signals continuously. The ESP32-S3 acts as the receiver and measures exactly how those signals change as they bounce around the room. When a human moves, they disturb the signal in a unique way. We convert that disturbance into an image (spectrogram) and classify it using a neural network.

---

## Key Definitions

* **CSI (Channel State Information):** Detailed information about how a Wi-Fi signal travels from transmitter to receiver. Unlike RSSI, CSI captures amplitude, phase, and frequency response across every individual subcarrier, providing a rich fingerprint of the environment.
* **OFDM Subcarrier:** Wi-Fi splits its signal into many narrow frequency bands called subcarriers. The ESP32-S3 exposes 52 of them. Each is affected differently by objects in the room, making CSI highly informative.
* **RSSI (Received Signal Strength Indicator):** A single number representing received Wi-Fi signal strength. It is too coarse for detecting subtle human presence compared to CSI.
* **Multipath Propagation:** Wi-Fi signals reflect off walls, furniture, and people, arriving at the receiver via multiple paths. CSI captures all these reflections simultaneously, making it sensitive to human movement.
* **LOS / NLOS (Line-of-Sight / Non-Line-of-Sight):** LOS means a direct path between transmitter and receiver. NLOS means an obstruction blocks the path. Our system works reliably in both conditions.
* **STFT (Short-Time Fourier Transform):** A mathematical tool that converts a 1D signal into a 2D time-frequency image called a spectrogram.
* **Spectrogram:** A grayscale image (52 × 400 pixels in our system) where each column is a snapshot in time and each row is one of the 52 subcarriers. Pixel intensity represents signal amplitude.
* **EfficientNetV2:** A state-of-the-art Convolutional Neural Network (CNN) modified to accept single-channel grayscale spectrograms and retrained on our CSI dataset.
* **FreeRTOS:** A real-time operating system for microcontrollers that lets the ESP32 run tasks concurrently (capturing CSI and sending data over UART) safely.
* **CRC-16/CCITT:** A checksum algorithm that detects transmission errors to ensure clean data enters the pipeline.
* **Hampel Filter:** A statistical outlier-removal filter that replaces sudden noise spikes in the CSI data before transmission.
* **WeightedRandomSampler:** A PyTorch training utility that ensures the model sees each activity class equally often during training to prevent bias.
* **Supabase:** An open-source database used to log every inference result and store spectrogram images.

---

## Hardware

### Why ESP32?
While chips like the Artemis series, BCM series, and some Intel Wi-Fi cards expose CSI, we chose the ESP32 family because it is inexpensive, well-documented, and supported by ESP-IDF (which provides direct access to the CSI callback API).

Our first-generation capture rig used four off-the-shelf ESP32-S3 WROOM modules. We have since designed a **custom dual-receiver PCB** to replace them, described below.

### Custom Dual-Receiver CSI Board (Rev. 2)

Off-the-shelf dev boards proved to be the limiting factor in CSI quality. Their PCB antennas cannot be replaced, their ground planes are shared with USB and power circuitry, and every unit sits on a separate USB cable with its own power supply — all of which inject amplitude noise into exactly the measurement we care about. The custom board addresses each of these.

**Two independent receivers on one board.** Both ESP32s operate in receive-only mode and capture CSI from the same ambient Wi-Fi traffic, giving two spatially separated measurements of the same environment from a single unit.

**Key specifications**

| Item | Detail |
|---|---|
| Board | 4-layer, 55 × 55 mm |
| Stackup | F.Cu / 0.12 mm 2116 prepreg / **In1.Cu solid GND** / 1.165 mm core / **In2.Cu +3V3** / 0.12 mm / B.Cu |
| MCUs | 2 × ESP32, each with its own 40 MHz crystal and AT25SF081 SPI flash |
| Antennas | 2 × SMA connector for external antennas (bi-quad or other) |
| RF feed | 50 Ω controlled-impedance microstrip, 0.20 mm wide |
| Matching | Pi network (1.5 pF / 2 nH / 1.5 pF) per channel, with spare DNP positions for bench tuning |
| USB | USB-C → CH334F hub → 2 × CP2102N UART bridges (one COM port per ESP32) |
| Power | AP63203 buck (5 V → 3.3 V), 2 A polyfuse, PMEG2020EH reverse-protection Schottky |
| Protection | USBLC6-4SC6 ESD array on USB data lines, ESD diodes on EN/BOOT |
| Programming | Cross-coupled DTR/RTS auto-reset per channel (standard esptool circuit) |
| Draw | ~290 mA at 5 V with both receivers active — within the 500 mA USB budget |

**Why the layer stack matters.** A dedicated uninterrupted ground plane 0.12 mm below the signal layer gives every RF and USB trace a clean return path. Nothing is routed on the ground layer. Via inductance from any component to ground is roughly 0.04 nH, so the antenna matching network behaves as designed rather than being swamped by parasitics.

**Antenna launch design.** Each SMA connector is surrounded by a ring of ground vias so the return current reaches the plane immediately. A deliberate void in the ground plane beneath each SMA signal pad removes ~2.3 pF of parasitic capacitance that would otherwise present a 12 Ω discontinuity at the connector. Measured effect: launch insertion loss drops from ~3.3 dB to ~0.1 dB per channel, and both channels are symmetric to within a few tenths of a dB — important when comparing amplitude between the two receivers.

**Single power source.** Both receivers share one regulated 3.3 V rail from a single USB connection, rather than two separately powered dev boards. One cable, one supply, one ground reference.

**Custom Bi-Quad Antenna:** A custom-designed 2.4 GHz directional antenna connects via the SMA ports to provide higher gain and improved CSI stability than a stock PCB antenna.

**Source code and hardware files:** [github.com/irocobble/CSI_collection](https://github.com/irocobble/CSI_collection/tree/main)

### Known Design Limitations

Documented deliberately so future revisions can address them:

* **No cross-receiver phase coherence.** The two ESP32s run from independent crystals, so their carrier and sampling frequency offsets drift relative to each other. This is fine for our amplitude-only pipeline but rules out phase-based techniques such as AoA or MUSIC across the two antennas. A shared 40 MHz TCXO feeding both `XTAL_P` pins would fix this in a future revision.
* **No GPIO breakout.** No general-purpose pins are brought out, so there is no hardware trigger, PPS input, or sync line for correlating CSI against external ground truth. Timestamping is currently done in software over USB.
* **1 MB flash (AT25SF081).** Sufficient for the capture firmware but leaves no room for OTA update partitions.

### Bring-Up Checklist

For anyone assembling a board, test in this order and stop at the first failure — it localises the fault far faster than powering everything at once:

1. **Power only**, no USB data — confirm 3.3 V, check input voltage at the buck, scope the rail for ripple.
2. **Plug USB** — does the hub enumerate? If not, fit the DNP crystal load capacitors (C4/C23, 12 pF).
3. **Check both COM ports appear.** A missing port isolates to that CP2102N or its hub port.
4. **Flash each ESP32** — auto-reset should work without holding buttons.
5. **Scope VDD3P3 during RX.** More than ~50 mV of ripple → populate C8/C42 (1 µF).
6. **VNA the antenna ports** and tune the pi network. This step determines CSI amplitude quality more than anything else.
7. **Capture CSI** and inspect the noise floor across all 52 subcarriers before trusting any measurement.

Keep spare 12 pF 0402, 1 µF 0603, and 12 pF-load 40 MHz crystals on hand for steps 2, 5, and any oscillator trouble.

---

## Firmware — ESP-IDF Setup

### Installation

**Linux**
1.  Make a new directory: `mkdir -p ~/esp`
2.  Get to the directory: `cd ~/esp`
3.  Clone the files from the official ESP git repo:
    `git clone --recursive https://github.com/espressif/esp-idf.git`
4.  Get in the directory: `cd esp-idf`
5.  Install the esp-idf: `./install.sh`

**Windows**
1.  Go to: [https://dl.espressif.com/dl/esp-idf/](https://dl.espressif.com/dl/esp-idf/)
2.  Download and install the universal online installer.

### Activating The Environment
1.  Activate the esp-IDE environment by running export.sh:
    `source ~/esp/esp-idf/export.sh` (Run once per terminal session)
2.  Go to the esp directory: `cd ~/esp`
3.  Run to create an ESP Project: `idf.py create-project my_project`
4.  Go to Project Directory: `cd my_project`

### Required Project File Structure (Crucial!)
The project must follow this layout exactly:

```
my_project/
├── CMakeLists.txt      # lists component directories
├── sdkconfig           # auto-generated after menuconfig
└── main/
    ├── CMakeLists.txt  # registers main.c as a component
    └── main.c          # your application code
```

### Menuconfig (The Heart of ESP-IDE)
Run: `idf.py menuconfig`
Common configurations for this project:
* **Enable CSI:** Component config → Wi-Fi → [*] Enable CSI
* **SSID + Password:** Wi-Fi settings → set the SSID and password.

### Build, Flash, and Monitor
1.  Activate ESP-IDE on bash.
2.  Set the chip target — this **must** match the silicon on the board:
    `idf.py set-target esp32` (use `esp32s3` for the older WROOM-module rig)
3.  Go to the root directory of the Project.
4.  Compile: `idf.py build`
5.  Flash: `idf.py -p /dev/ttyUSB0 flash` (replace port as needed)
6.  Monitor Serial Output: `idf.py monitor` (Ctrl+] to exit)

**Note on the custom board:** it presents **two** serial ports, one per receiver. Flash each independently, e.g. `/dev/ttyUSB0` and `/dev/ttyUSB1`.

### How the Firmware Works
The firmware runs two concurrent FreeRTOS tasks:
1.  **CSI callback:** Fires on every received Wi-Fi packet. Extracts the amplitude of 52 subcarriers, applies a Hampel filter to remove spike outliers, and writes the cleaned sample into a shared frame buffer protected by a FreeRTOS mutex.
2.  **UART TX task:** Once 400 samples accumulate (forming a 52×400 frame), it wraps the payload, computes a CRC-16/CCITT checksum, and transmits at 921,600 baud.

---

## Software Pipeline — Python

### Overview
**Serial read** → **CRC validation** → **Reshape 52×400** → **PNG export** → **API inference** → **Supabase log** → **Dashboard**

* **Serial Ingestion:** A `FrameReader` class maintains a buffer and scans for frames, validating the CRC-16 checksum to ensure clean data.
* **Multi-device Auto-detection:** A background thread scans COM ports to automatically detect active ESP32 units. On the custom board this discovers both on-board receivers as separate ports.
* **Image Construction:** The ESP-32 provides CSI data as a matrix. We convert this 52×400 matrix (200 samples in X-axis and 52 amplitude values in Y-axis) into a 52*400 image. The black and white density represents the amplitude change.
* **Asynchronous Inference & Re-inference:** Python handles real-time inference via an API without blocking the serial reader and includes a GUI for manual re-inference on previously captured frames.

---

## Machine Learning Pipeline

### Dataset
A custom dataset was collected using the four ESP32-S3 units in real indoor environments (LOS and NLOS).
* **Labels:** `trainLabels.csv`, `validationLabels.csv`, `testLabels.csv`
* **Normalization:** `meanStd.csv` is used to apply identical normalization `(x − mean) / std`.

> **Note:** Data captured on the custom board has a different RF front end (external antenna, different ground reference, different launch loss) from the WROOM-module rig. Recompute `meanStd.csv` for the new hardware, and treat the two sources as separate domains until a mixed-hardware model is validated.

### Preprocessing and Augmentation
1.  Grayscale conversion
2.  Tensor conversion ([0, 255] to [0, 1])
3.  Normalization
4.  Random circular shift (training only)
5.  Gaussian noise injection (training only)

### Model (The AI-ML Part)
**Backbone:** EfficientNetV2-S. This is a lightweight, efficient CNN that helps us find key details in the small images we generate. The first convolution layer is modified to accept 1-channel (grayscale) input.

### Training Configuration
* **Loss function:** CrossEntropyLoss
* **Optimiser:** Adam (LR: 0.0001)
* **LR scheduler:** ReduceLROnPlateau
* **Early stopping:** Halts if validation accuracy does not improve for 20 epochs.
* **Class balancing:** WeightedRandomSampler
* **Hardware:** NVIDIA RTX 4060 with CUDA, via PyTorch.

---

## System Integration
* **Flask REST API:** Inference results are served via a Flask REST endpoint.
* **Real-time Web Dashboard:** Displays current activity label and confidence score.
* **Flutter Mobile App:** Cross-platform mobile application mirroring the dashboard.
* **Supabase Cloud Logging:** Every inference result and image is asynchronously inserted into a Supabase Postgres table and storage bucket.

---

## Limitations and Future Work

**Current limitations:** The CNN treats the spectrogram as a static image without explicitly modeling temporal sequences. Performance varies between LOS and NLOS environments when trained exclusively on one. The dataset size is relatively small, and inference currently requires a dedicated PC with a GPU. The custom board's two receivers are amplitude-only and cannot be combined coherently (see Known Design Limitations above).

# Future Work & System Architectures

As the project evolves from a tethered PC-based prototype to a standalone edge-computing device, we are evaluating four distinct architectural paths. Each path offers different trade-offs regarding power consumption, computational capability, and RF flexibility.

## 0. Immediate Next Revision (Rev. 3 of the custom board)
**Concept:** Incremental improvements to the existing dual-receiver design, identified during Rev. 2 design review.
* **Shared clock:** One 40 MHz TCXO buffered to both ESP32s, eliminating relative CFO/SFO and unlocking phase-based processing across the two antennas.
* **GPIO header:** Break out 2–3 pins per chip for external triggering and timestamp synchronisation with ground-truth sensors.
* **Larger flash:** 4 MB part to allow OTA updates in deployed nodes.
* **Per-radio supply filtering:** Ferrite bead and local bulk capacitance on each receiver's 3.3 V feed, so neither radio's supply transients appear in the other's measurement.

## 1. The Microcontroller Ensemble (ESP32-C5 → RP2040 → ESP32-S3)
**Concept:** A fully independent, distributed TinyML system using specialized microcontrollers for each stage of the pipeline.
* **CSI Acquisition (ESP32-C5):** Utilizes dual-band Wi-Fi 6 capabilities. The 5GHz band offers shorter wavelengths, providing higher sensitivity to micro-movements.
* **Signal Processing (RP2040):** Acts as a dedicated DSP to ingest raw amplitude data and perform STFT to generate spectrogram matrices.
* **Inference & IO (ESP32-S3):** Runs a quantized version of the EfficientNetV2 model (via ESP-DL or TFLite Micro) for prediction and drives indication sensors (LEDs, relays, buzzers).
* **Pros:** Ultra-low power, low cost, highly modular, pure edge architecture.
* **Cons:** High risk of data bottlenecks via SPI/UART between three chips. The RP2040 lacks a hardware FPU, potentially slowing down spectrogram generation.

## 2. The Hybrid Edge (ESP32-C5 → Pi Compute Module 5)
**Concept:** Pairing a dedicated CSI sensor with a high-performance Linux edge computer.
* **Workflow:** The ESP32-C5 streams raw CSI data directly to the CM5 via high-speed interfaces. The CM5 handles STFT generation and deep learning inference natively in Python.
* **Pros:** Retains the exact Python/PyTorch software stack currently used on the PC. Massive processing headroom. Eliminates inter-MCU bottlenecks.
* **Cons:** Higher power footprint. Requires designing a custom carrier PCB for the CM5 module.

## 3. High-Resolution MIMO (Intel / Atheros Wi-Fi Cards)
**Concept:** Migrating to standard PCIe Wi-Fi network interface cards running patched Linux kernels (e.g., Linux 802.11n CSI Tool).
* **Workflow:** Utilizing specific chipsets (Intel 5300 or Atheros AR9300) on a Single Board Computer (SBC) to extract highly detailed CSI matrices.
* **Pros:** Unlocks MIMO (Multiple-Input Multiple-Output) data. Capturing multiple spatial streams simultaneously drastically improves environmental mapping and enables directional movement tracking.
* **Cons:** Not scalable as a compact IoT device. Requires painful kernel patching, often locking the system to outdated OS versions and legacy hardware.

## 4. Raw RF Processing (Software Defined Radio - SDR)
**Concept:** Ditching the 802.11 Wi-Fi protocol entirely to capture raw baseband IQ data using hardware like bladeRF or HackRF.
* **Workflow:** Transmitting custom waveforms and analyzing raw reflections without the constraints of Wi-Fi packet structures.
* **Pros:** Ultimate flexibility. Allows operation outside standard Wi-Fi bands (e.g., sub-GHz for superior wall penetration). Total control over bandwidth, modulation, and timing.
* **Cons:** Steep learning curve requiring advanced DSP and RF engineering mathematics. SDRs are expensive and generate massive data throughput requiring significant processing power.
* Edge inference on ESP32-S3 or Jetson Nano
* Multi-node distributed sensing
