import json
from datetime import datetime, timedelta
import os

APPOINTMENTS_FILE = 'appointments.json'
DAYS_AHEAD = 30
SLOT_TIMES = [
    "10:00-10:30", "10:30-11:00", "11:00-11:30", "11:30-12:00",
    "12:00-12:30", "12:30-13:00", "14:00-14:30", "14:30-15:00"
]

def load_appointments():
    if os.path.exists(APPOINTMENTS_FILE):
        with open(APPOINTMENTS_FILE, 'r') as f:
            return json.load(f)
    return {"doctors": []}

def save_appointments(data):
    with open(APPOINTMENTS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def generate_schedule(existing_schedule, today):
    # Keep only today and future dates
    future_schedule = [s for s in existing_schedule if s['date'] >= today.strftime('%Y-%m-%d')]
    # Get all dates in future_schedule
    existing_dates = set(s['date'] for s in future_schedule)
    # Add new days at the end
    for i in range(DAYS_AHEAD):
        date = (today + timedelta(days=i)).strftime('%Y-%m-%d')
        if date not in existing_dates:
            slots = [{"time": t, "available": True} for t in SLOT_TIMES]
            future_schedule.append({"date": date, "slots": slots})
    # Sort by date
    future_schedule.sort(key=lambda s: s['date'])
    return future_schedule

def main():
    today = datetime.today().date()
    data = load_appointments()
    for doc in data.get('doctors', []):
        doc['schedule'] = generate_schedule(doc.get('schedule', []), today)
    save_appointments(data)
    print(f"appointments.json updated for {DAYS_AHEAD} days ahead.")

if __name__ == '__main__':
    main() 