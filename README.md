# CSI_collection
capturing CSI data using ESP_32_S3
This Project Aims to Capture CSI data using 4 Identically made ESP32 -s3 wroom 
Here we are using custom made ESP bords and antennas to get CSI data 

Channel State Information (CSI) describes how a wireless signal propagates from a transmitter to a receiver across a communication channel.
Instead of measuring only signal strength like RSSI, CSI captures detailed information about:
  signal amplitude
  signal phase
  multipath reflections
  frequency response per subcarrier
CSI provides a fine-grained representation of how the environment affects the Wi-Fi signal.

An ESP-32 has 52 Subcarrirs so the CSI data which we get is a matrix of 208 is produces each time
now we take this matrix and convert it into a graph of 200 samples in X-axis and 52 amplitude values in Y-axis the black and white density represets the amplitude change

How to use esp-idf
installation: 
Linux 
1] Make a new directory: “mkdir -p ~/<name>”
2] get to the directory: “cd ~/esp”
3] Clone the files from official ESP git repo: “git clone --recursive https://github.com/espressif/esp-idf.git”
4] get in the directory: “cd esp-idf”
5] install the esp-idf: “./install.sh”
Windows 
1] go to- https://dl.espressif.com/dl/esp-idf/
2] Download and install universal online installer 

Activating The Environment
1] activate the esp-IDE environment by running export.sh: “source export.sh”
2] create a new directory for the project: “cd ~/esp”
3] Run to create an ESP Project: “idf.py create-project <name>”
4] Go to Project Directory: cd <name>
How the Project File must look like(Really Important!!)
my_project/
|-- CMakeLists.txt <- must contain the file directory  
|-- sdkconfig <- Manually add Dependency 
|-- main/
│     |-- CMakeLists.txt <- This also contains your main directory
│     |--  main.c   <-- YOUR CODE HERE
Project File must be like this nahi to chalega to nahi saar dard dega alag
Menuconfig (The Heart of ESP-IDE)
Cmd to open menuconfig: idf.py menuconfig
Here You can configure How your esp must function 
Common Configuration For this Project: -> 
o	Component config →  Wi-Fi →  [*] Enable CSI
o	CSI Wifi Setting → set SSID and Password
Building your main File
1] Activate ESP-IDE on bash
2] Go to root directory of the Project
3] Run the following cmd : idf.py build
4] Flash your ESP: idf.py -p flash
5] Monitor the Serial Output: idf.py monitor

