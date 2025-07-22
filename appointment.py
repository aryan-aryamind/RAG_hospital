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

    def find_doctors_by_name(self, name: str) -> list:
        """Return a list of doctors matching the given name (case-insensitive), regardless of department."""
        return [doc for doc in self.schedule["doctors"] if doc["name"].lower() == name.lower()]

    def check_availability(self, doctor_name: str, department: str, date: str, time: str) -> bool:
        doc = self.find_doctor(doctor_name, department)
        if not doc:
            return False
        for sch in doc["schedule"]:
            if sch["date"] == date:
                for slot in sch["slots"]:
                    if slot["time"] == time and slot["available"]:
                        return True
        return False

    def book_slot(self, doctor_name: str, department: str, date: str, time: str, user_name: str, user_mobile: str) -> bool:
        # --- Enforce booking window: today to 2 months from today ---
        try:
            from datetime import datetime, timedelta
            parsed_date = None
            # Try to parse date as date
            for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%d %B %Y', '%d %b %Y', '%d %B, %Y', '%d %b, %Y', '%d-%b-%Y', '%d-%B-%Y'):
                try:
                    parsed_date = datetime.strptime(date, fmt).date()
                    break
                except Exception:
                    continue
            if not parsed_date:
                # fallback: try parsing as is
                try:
                    parsed_date = datetime.fromisoformat(date).date()
                except Exception:
                    pass
            if parsed_date:
                today = datetime.today().date()
                two_months_later = today + timedelta(days=61)
                if parsed_date < today or parsed_date > two_months_later:
                    return False
        except Exception:
            pass
        doc = self.find_doctor(doctor_name, department)
        if not doc:
            return False
        for sch in doc["schedule"]:
            if sch["date"] == date:
                for slot in sch["slots"]:
                    if slot["time"] == time and slot["available"]:
                        slot["available"] = False
                        slot["booked_by"] = user_name
                        slot["mobile"] = user_mobile
                        slot["booked_at"] = datetime.now().isoformat()
                        self._save_schedule()
                        return True
        return False

    def suggest_alternative(self, doctor_name: str, department: str, date: str, time: str) -> Optional[Dict[str, str]]:
        doc = self.find_doctor(doctor_name, department)
        if not doc:
            return None
        # Find the closest available slot on the same date
        for sch in doc["schedule"]:
            if sch["date"] == date:
                for slot in sch["slots"]:
                    if slot["available"]:
                        return {"date": date, "time": slot["time"]}
        # Optionally, look at other dates
        for sch in doc["schedule"]:
            for slot in sch["slots"]:
                if slot["available"]:
                    return {"date": sch["date"], "time": slot["time"]}
        return None

    def reschedule(self, doctor_name: str, department: str, old_date: str, old_time: str, new_date: str, new_time: str, user_name: str, user_mobile: str) -> bool:
        # Find and check the old booking
        doc = self.find_doctor(doctor_name, department)
        if not doc:
            return False
        old_slot = None
        for sch in doc["schedule"]:
            if sch["date"] == old_date:
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
        if not self.check_availability(doctor_name, department, new_date, new_time):
            return False
        # Book new slot
        if not self.book_slot(doctor_name, department, new_date, new_time, user_name, user_mobile):
            return False
        # Free old slot
        old_slot["available"] = True
        old_slot.pop("booked_by", None)
        old_slot.pop("mobile", None)
        old_slot.pop("booked_at", None)
        self._save_schedule()
        return True

    def get_departments(self) -> list:
        """Return a list of all unique departments."""
        return list(set(doc["department"] for doc in self.schedule["doctors"]))

    def get_doctors_by_department(self, department: str) -> list:
        """Return a list of doctor names in the given department (case-insensitive)."""
        return [doc["name"] for doc in self.schedule["doctors"] if doc["department"].lower() == department.lower()]

    def find_available_doctor(self, department: str, date: str, time: str) -> Optional[dict]:
        """Return a doctor in the department available at the given date and time, or None."""
        for doc in self.schedule["doctors"]:
            if doc["department"].lower() == department.lower():
                for sch in doc["schedule"]:
                    if sch["date"] == date:
                        for slot in sch["slots"]:
                            if slot["time"] == time and slot["available"]:
                                return {"doctor": doc["name"], "department": department, "date": date, "time": time}
        return None

    def suggest_nearest_slot(self, department: str, date: str, time: str) -> Optional[dict]:
        """Suggest the nearest available slot for any doctor in the department on the given date, or on other dates if none available."""
        # First, try same date, any doctor
        for doc in self.schedule["doctors"]:
            if doc["department"].lower() == department.lower():
                for sch in doc["schedule"]:
                    if sch["date"] == date:
                        for slot in sch["slots"]:
                            if slot["available"]:
                                return {"doctor": doc["name"], "department": department, "date": date, "time": slot["time"]}
        # Then, try other dates
        for doc in self.schedule["doctors"]:
            if doc["department"].lower() == department.lower():
                for sch in doc["schedule"]:
                    for slot in sch["slots"]:
                        if slot["available"]:
                            return {"doctor": doc["name"], "department": department, "date": sch["date"], "time": slot["time"]}
        return None

    def get_available_doctors(self, department: str, date: str, time: str) -> list:
        """Return a list of doctor names available in the department at the given date and time."""
        available = []
        for doc in self.schedule["doctors"]:
            if doc["department"].lower() == department.lower():
                for sch in doc["schedule"]:
                    if sch["date"] == date:
                        for slot in sch["slots"]:
                            if slot["time"] == time and slot["available"]:
                                available.append(doc["name"])
        return available

    def get_available_doctors_by_date(self, department: str, date: str, time: str) -> list:
        """Return a list of doctor names available in the department at the given date and time."""
        return self.get_available_doctors(department, date, time) 