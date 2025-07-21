import json
import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

SCHEDULE_FILE = os.path.join(os.path.dirname(__file__), 'appointments.json')

class AppointmentManager:
    def __init__(self, schedule_file: str = SCHEDULE_FILE):
        self.schedule_file = schedule_file
        self._load_schedule()

    def _load_schedule(self):
        if not os.path.exists(self.schedule_file):
            self.schedule = {"doctors": []}
            self._save_schedule()
        else:
            with open(self.schedule_file, 'r') as f:
                self.schedule = json.load(f)

    def _save_schedule(self):
        with open(self.schedule_file, 'w') as f:
            json.dump(self.schedule, f, indent=2)

    def find_doctor(self, name: str, department: Optional[str] = None) -> Optional[Dict[str, Any]]:
        for doc in self.schedule["doctors"]:
            if doc["name"].lower() == name.lower() and (department is None or doc["department"].lower() == department.lower()):
                return doc
        return None

    def check_availability(self, doctor_name: str, department: str, day: str, time: str) -> bool:
        doc = self.find_doctor(doctor_name, department)
        if not doc:
            return False
        for sch in doc["schedule"]:
            if sch["day"].lower() == day.lower():
                for slot in sch["slots"]:
                    if slot["time"] == time and slot["available"]:
                        return True
        return False

    def book_slot(self, doctor_name: str, department: str, day: str, time: str, user_name: str, user_mobile: str) -> bool:
        doc = self.find_doctor(doctor_name, department)
        if not doc:
            return False
        for sch in doc["schedule"]:
            if sch["day"].lower() == day.lower():
                for slot in sch["slots"]:
                    if slot["time"] == time and slot["available"]:
                        slot["available"] = False
                        slot["booked_by"] = user_name
                        slot["mobile"] = user_mobile
                        slot["booked_at"] = datetime.now().isoformat()
                        self._save_schedule()
                        return True
        return False

    def suggest_alternative(self, doctor_name: str, department: str, day: str, time: str) -> Optional[Dict[str, str]]:
        doc = self.find_doctor(doctor_name, department)
        if not doc:
            return None
        # Find the closest available slot on the same day
        for sch in doc["schedule"]:
            if sch["day"].lower() == day.lower():
                for slot in sch["slots"]:
                    if slot["available"]:
                        return {"day": day, "time": slot["time"]}
        # Optionally, look at other days
        for sch in doc["schedule"]:
            for slot in sch["slots"]:
                if slot["available"]:
                    return {"day": sch["day"], "time": slot["time"]}
        return None

    def reschedule(self, doctor_name: str, department: str, old_day: str, old_time: str, new_day: str, new_time: str, user_name: str, user_mobile: str) -> bool:
        # Find and check the old booking
        doc = self.find_doctor(doctor_name, department)
        if not doc:
            return False
        old_slot = None
        for sch in doc["schedule"]:
            if sch["day"].lower() == old_day.lower():
                for slot in sch["slots"]:
                    if slot["time"] == old_time and not slot["available"] and slot.get("mobile") == user_mobile:
                        old_slot = slot
                        break
        if not old_slot:
            return False
        # Check if within 24 hours
        booked_at = datetime.fromisoformat(old_slot["booked_at"])
        if datetime.now() - booked_at > timedelta(hours=24):
            return False
        # Check new slot availability
        if not self.check_availability(doctor_name, department, new_day, new_time):
            return False
        # Book new slot
        if not self.book_slot(doctor_name, department, new_day, new_time, user_name, user_mobile):
            return False
        # Free old slot
        old_slot["available"] = True
        old_slot.pop("booked_by", None)
        old_slot.pop("mobile", None)
        old_slot.pop("booked_at", None)
        self._save_schedule()
        return True 