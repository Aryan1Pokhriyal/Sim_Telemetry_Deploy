#!/bin/bash

# === LOAD CONFIG ===
CONFIG_FILE="config.txt"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "config.txt not found. Exiting."
    exit 1
fi
source <(grep = "$CONFIG_FILE")

PROJECT_DIR="$(pwd)"
echo "Running setup in current directory: $PROJECT_DIR"

echo "Installing dependencies..."
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y \
  python3 python3-pip python3-venv \
  mosquitto mosquitto-clients \
  network-manager

# === Disable Conflicting Services ===
echo "Disabling dhcpcd, hostapd, dnsmasq..."
sudo systemctl disable --now dhcpcd
sudo systemctl disable --now hostapd
sudo systemctl disable --now dnsmasq

# === Enable NetworkManager ===
echo "Enabling and starting NetworkManager..."
sudo systemctl enable NetworkManager
sudo systemctl start NetworkManager

# === Set up Access Point via NetworkManager (wlan0) ===
if [[ "$SETUP_AP" == "true" ]]; then
  echo "Setting up Access Point using NetworkManager on wlan0..."

  nmcli connection add type wifi ifname wlan0 con-name "$AP_SSID" autoconnect yes ssid "$AP_SSID"
  nmcli connection modify "$AP_SSID" 802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared
  nmcli connection modify "$AP_SSID" wifi-sec.key-mgmt wpa-psk
  nmcli connection modify "$AP_SSID" wifi-sec.psk "$AP_PASSWORD"

  nmcli connection down "$AP_SSID" || true
  nmcli connection up "$AP_SSID"
fi

# === MQTT Broker Setup ===
echo "Restarting Mosquitto broker..."
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto

# === Python App Setup ===
echo "Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Creating required files..."
touch incoming.txt to_send.txt ledger.txt logs.txt

# === Startup Script ===
cat <<EOF > start_project.sh
#!/bin/bash
cd "$PROJECT_DIR"
source venv/bin/activate
python3 main.py >> logs.txt 2>&1
EOF
chmod +x start_project.sh

# === Cron Autostart ===
echo "Adding startup to crontab..."
(crontab -l 2>/dev/null | grep -v 'start_project.sh'; echo "@reboot $PROJECT_DIR/start_project.sh") | crontab -

# === Connect wlan1 to external Wi-Fi if specified ===
if [[ -n "$WIFI_SSID" && -n "$WIFI_PASSWORD" ]]; then
  echo "Connecting wlan1 to external Wi-Fi: $WIFI_SSID..."

  # Scan first to make sure network is visible
  nmcli dev wifi list ifname wlan1

  # Connect via wlan1
  nmcli device wifi connect "$WIFI_SSID" password "$WIFI_PASSWORD" ifname wlan1

  # Set autoconnect
  nmcli connection modify "$WIFI_SSID" connection.autoconnect yes

  echo "wlan1 is now connected to $WIFI_SSID and will auto-connect on reboot."
else
  echo "ℹExternal Wi-Fi credentials not provided in config.txt — skipping wlan1 setup."
fi

echo "All setup complete. Reboot the Pi to start the system."
