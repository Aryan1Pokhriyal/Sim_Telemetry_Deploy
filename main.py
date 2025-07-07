import json
import time
import os
import threading
import requests
import paho.mqtt.client as mqtt

file1 = "incoming.txt"
file2 = "to_send.txt"
file3 = "ledger.txt"
api_url = "https://apis.therefor.in/api/ingest-sensor-data-V2"
lock = threading.Lock()
prune_age_minutes = 60  # Age after which stale cloudStatus=0 entries are pruned

# ------------------------------------
# Utility Functions
# ------------------------------------
def transform_data(data, cloudStatus):
    machine = data.get("machine")
    mac = data.get("mac")
    raw_values = data.get("valuesList", [])
    converted_values = []

    for item in raw_values:
        if isinstance(item, dict):
            for k, v in item.items():
                converted_values.append({"name": k, "value": v})

    return {
        "consolidatorId": "string",
        "recordedTime": int(time.time()),
        "machine": str(machine),
        "mac": mac,
        "cloudStatus": cloudStatus,
        "valuesList": converted_values
    }

def get_time_value(data):
    for v in data.get("valuesList", []):
        if v.get("name") == "time":
            return v.get("value")
    return None

def match_entry_by_time_and_machine(a, b):
    return (a.get("machine") == b.get("machine") and get_time_value(a) == get_time_value(b))

def append_json_to_file(filename, data):
    with lock:
        with open(filename, "a") as f:
            f.write(json.dumps(data) + "\n")

def append_multiple_to_file(filename, entries):
    with lock:
        with open(filename, "a") as f:
            for d in entries:
                f.write(json.dumps(d) + "\n")

def read_all_lines(filename):
    with lock:
        if not os.path.exists(filename):
            return []
        with open(filename, "r") as f:
            return [json.loads(line.strip()) for line in f if line.strip()]

def write_all_lines(filename, json_list):
    with lock:
        with open(filename, "w") as f:
            for d in json_list:
                f.write(json.dumps(d) + "\n")

# ------------------------------------
# Thread 1: MQTT Listener
# ------------------------------------
def mqtt_listener_thread():
    def on_message(client, userdata, msg):
        try:
            incoming = json.loads(msg.payload.decode())
            base = transform_data(incoming, 0)

            append_json_to_file(file3, base)
            base["cloudStatus"] = 1
            append_json_to_file(file2, base)

            print(f"Received: Time={get_time_value(base)} Machine={base['machine']}")
        except Exception as e:
            print(f"MQTT Error: {e}")

    client = mqtt.Client()
    client.on_message = on_message
    client.connect("localhost", 1883)
    client.subscribe("Test15X_/sensorData")
    client.loop_forever()

# ------------------------------------
# Thread 2: API Sender
# ------------------------------------
def api_sender_thread(batch_time=60):
    print("STARTING SENDER THREAD")
    while True:
        try:
            time.sleep(batch_time)
            print("--- File Status ---")
            print(f"[Incoming file] = = {len(read_all_lines(file1))} entries")
            print(f"[Outgoing file] = = {len(read_all_lines(file2))} entries")
            print(f"[Ledger file] = = {len(read_all_lines(file3))} entries")

            all_data = read_all_lines(file2)
            if all_data:
                print(f"Sending batch of size {len(all_data)}")
                print("Posting to:", api_url)
                try:
                    response = requests.post(api_url, json=all_data, timeout=60)
                    print(f"Response code: {response.status_code}")

                    if response.status_code == 200:
                        try:
                            result = response.json()
                            stored = result.get("stored", [])
                            print(f"Batch sent. Stored count: {len(stored)}")

                            # Prune matching entries from ledger (cloudStatus 0 only)
                            ledger = read_all_lines(file3)
                            updated_ledger = [d for d in ledger if not (
                                d.get("cloudStatus") == 0 and any(match_entry_by_time_and_machine(d, s) for s in stored)
                            )]
                            write_all_lines(file3, updated_ledger)

                        except Exception as e:
                            print(f"JSON decode failed: {e}")

                    # Clear to_send file regardless of success
                    write_all_lines(file2, [])

                except Exception as e:
                    print(f"Error sending batch: {e}")

        except Exception as outer_e:
            print(f"Outer loop error: {outer_e}")

# ------------------------------------
# Thread 3: Retry Unconfirmed
# ------------------------------------
def retry_unconfirmed_thread():
    while True:
        time.sleep(60)
        ledger = read_all_lines(file3)
        now = int(time.time())
        retry = []
        retained_ledger = []

        for entry in ledger:
            if entry.get("cloudStatus") == 1:
                retry.append(entry)
                retained_ledger.append(entry)
            elif entry.get("cloudStatus") == 0:
                age = now - entry.get("recordedTime", now)
                if age < prune_age_minutes * 60:
                    retained_ledger.append(entry)
                else:
                    print("Pruned stale unconfirmed entry")
            else:
                retained_ledger.append(entry)

        if retry:
            append_multiple_to_file(file2, retry)
            print(f"Requeued {len(retry)} unsent entries.")

        write_all_lines(file3, retained_ledger)

# ------------------------------------
# Run All Threads
# ------------------------------------
if __name__ == "__main__":
    threads = [
        threading.Thread(target=mqtt_listener_thread),
        threading.Thread(target=api_sender_thread),
        threading.Thread(target=retry_unconfirmed_thread)
    ]

    for t in threads:
        t.start()

    for t in threads:
        t.join()