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
