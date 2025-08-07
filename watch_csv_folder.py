import time
import os
import csv
import json
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Move csv_to_json function here

def csv_to_json(csv_path, json_path):
    data = []
    with open(csv_path, 'r', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            data.append(row)
    with open(json_path, 'w', encoding='utf-8') as jsonfile:
        json.dump(data, jsonfile, indent=2)
    print(f"Converted {csv_path} to {json_path}")

class CSVHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.csv'):
            filename = os.path.basename(event.src_path)
            hospital_name = filename.rsplit('.', 1)[0]
            json_path = os.path.join('upload', f"{hospital_name}.json")
            print(f"Detected new CSV: {filename}, converting to JSON...")
            csv_to_json(event.src_path, json_path)

if __name__ == "__main__":
    path = "upload_csv"
    event_handler = CSVHandler()
    observer = Observer()
    observer.schedule(event_handler, path, recursive=False)
    observer.start()
    print(f"Watching folder: {path} for new CSV files...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join() 