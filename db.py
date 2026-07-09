"""Single shared Mongo client and database handle.

user.py, workout_log.py, and audit.py each used to construct their own
AsyncIOMotorClient from MONGODB_URI — three connection pools to one database
and three import-time env reads. Every collection now hangs off this one
client; tests and tools get one seam to patch.
"""

import os

import motor.motor_asyncio

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")

mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
db = mongo_client["hey_garmin"]
