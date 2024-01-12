# GPU Fan Control Script

This repository contains a Bash script for controlling the fan speed of a GPU based on its temperature. The script reads the GPU temperature using `nvidia-smi` and adjusts the fan speed accordingly, using a linear interpolation algorithm. The PWM (Pulse Width Modulation) value for the fan speed is set in steps of 5 units.

## Script Details

The script `gpu-fan-control.sh` performs the following functions:
- Reads the current GPU temperature.
- Calculates the appropriate fan speed (PWM value) based on the GPU temperature.
- Sets the fan speed by writing to the PWM control file.
- In case of an error in reading the GPU temperature, sets the fan speed to a safe default value.

## Prerequisites

- The script requires `nvidia-smi` to be available on the system for reading GPU temperatures.
- Write access to the PWM control file (typically requires root privileges).

## Installation

1. Clone this repository or download the `gpu-fan-control.sh` script.
2. Place the script in a suitable directory, such as `/usr/local/bin/`:
   ```bash
   sudo cp gpu-fan-control.sh /usr/local/bin/
   sudo chmod +x /usr/local/bin/gpu-fan-control.sh
   ```

## Setting Up as a Systemd Service

To run the script as a service that starts on system boot:

1. **Create a Systemd Service File**: Create a file named `gpu-fan-control.service` in `/etc/systemd/system/`.

   ```bash
   sudo nano /etc/systemd/system/gpu-fan-control.service
   ```

   Add the following contents to the file:

   ```ini
   [Unit]
   Description=GPU Fan Control Service

   [Service]
   ExecStart=/usr/local/bin/gpu-fan-control.sh
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

2. **Reload Systemd**: Inform systemd that a new service file has been added:

   ```bash
   sudo systemctl daemon-reload
   ```

3. **Enable and Start the Service**: Enable the service to start on boot, and then start the service:

   ```bash
   sudo systemctl enable gpu-fan-control
   sudo systemctl start gpu-fan-control
   ```

4. **Check the Service Status**: Verify that the service is running:

   ```bash
   sudo systemctl status gpu-fan-control
   ```

## License

[Specify the license under which this project is available, e.g., MIT, GPL, etc.]

## Contributing

Contributions to this project are welcome. Please fork the repository and submit a pull request with your changes.

---

**Note**: This script is provided as is, and it comes with no guarantees. It's essential to test it thoroughly in your environment before using it in a production setup.
