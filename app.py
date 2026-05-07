import numpy as np
import sounddevice as sd
import threading
import time
import re
import sys
import os
import signal
import random
import collections
import configparser
import urllib.request
import json
import tempfile
from datetime import datetime, timezone, date
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO
from scipy import signal as dsp_signal
from scipy.fft import fft

# --- VERSION ---
VERSION = "v1.3a"

# --- SETTINGS ---
# Host API filtering: auto-detect based on OS, can be overridden via RDS_HOSTAPI env var
# On Windows, defaults to MME. On other platforms, no filtering.
# Set RDS_HOSTAPI=none to disable filtering, or RDS_HOSTAPI=CoreAudio/ALSA/etc to filter for specific API
_hostapi_env = os.environ.get("RDS_HOSTAPI", "").strip()
if _hostapi_env.lower() == "none":
    REQUIRE_HOSTAPI = None
elif _hostapi_env:
    REQUIRE_HOSTAPI = _hostapi_env
elif sys.platform == 'win32':
    REQUIRE_HOSTAPI = "MME"
else:
    REQUIRE_HOSTAPI = None  # No filtering on macOS/Linux by default

# --- RDS STANDARDS ---
BITRATE = 1187.5
SAMPLE_RATE = 192000
PILOT_FREQ = 19000
RDS_FREQ = 57000
G_POLY = 0x5B9
OFFSETS = {'A': 0x0FC, 'B': 0x198, 'C': 0x168, 'Cp': 0x350, 'D': 0x1B4}

# --- DAB CHANNELS (Group 12A ODA Linkage) ---
# Frequency code: (freq_MHz * 1000) / 16 = decimal value -> encode as 16-bit
DAB_CHANNELS = {
    "5A": 0x2AC5, "5B": 0x2B40, "5C": 0x2BBB, "5D": 0x2C36,
    "6A": 0x2CB1, "6B": 0x2D2C, "6C": 0x2DA7, "6D": 0x2E22,
    "7A": 0x2E9D, "7B": 0x2F18, "7C": 0x2F93, "7D": 0x300E,
    "8A": 0x3089, "8B": 0x3104, "8C": 0x317F, "8D": 0x31FA,
    "9A": 0x3275, "9B": 0x32F0, "9C": 0x336B, "9D": 0x33E6,
    "10A": 0x3461, "10B": 0x34DC, "10C": 0x3557, "10D": 0x35D2, "10N": 0x34EC,
    "11A": 0x364D, "11B": 0x36C8, "11C": 0x3743, "11D": 0x37BE, "11N": 0x36D8,
    "12A": 0x3839, "12B": 0x38B4, "12C": 0x392F, "12D": 0x39AA, "12N": 0x38C4,
    "13A": 0x3A25, "13B": 0x3AA0, "13C": 0x3B1B, "13D": 0x3B96, "13E": 0x3C11, "13F": 0x3C8C,
}

# --- RDS CHARACTER ENCODING (IEC 62106-4:2018 Table 5) ---
# RDS uses its own character set, NOT ISO-8859-1/Latin-1
# This table maps RDS byte codes (0x00-0xFF) to Unicode code points
RDS_TO_UNICODE = {
    0x00: 0x0000, 0x01: 0x0001, 0x02: 0x0002, 0x03: 0x0003,
    0x04: 0x0004, 0x05: 0x0005, 0x06: 0x0006, 0x07: 0x0007,
    0x08: 0x0008, 0x09: 0x0009, 0x0A: 0x000A, 0x0B: 0x000B,
    0x0C: 0x000C, 0x0D: 0x000D, 0x0E: 0x000E, 0x0F: 0x000F,
    0x10: 0x0010, 0x11: 0x0011, 0x12: 0x0012, 0x13: 0x0013,
    0x14: 0x0014, 0x15: 0x0015, 0x16: 0x0016, 0x17: 0x0017,
    0x18: 0x0018, 0x19: 0x0019, 0x1A: 0x001A, 0x1B: 0x001B,
    0x1C: 0x001C, 0x1D: 0x001D, 0x1E: 0x001E, 0x1F: 0x001F,
    0x20: 0x0020, 0x21: 0x0021, 0x22: 0x0022, 0x23: 0x0023,
    0x24: 0x00A4, 0x25: 0x0025, 0x26: 0x0026, 0x27: 0x0027,  # 0x24 is ¤ not $
    0x28: 0x0028, 0x29: 0x0029, 0x2A: 0x002A, 0x2B: 0x002B,
    0x2C: 0x002C, 0x2D: 0x002D, 0x2E: 0x002E, 0x2F: 0x002F,
    0x30: 0x0030, 0x31: 0x0031, 0x32: 0x0032, 0x33: 0x0033,
    0x34: 0x0034, 0x35: 0x0035, 0x36: 0x0036, 0x37: 0x0037,
    0x38: 0x0038, 0x39: 0x0039, 0x3A: 0x003A, 0x3B: 0x003B,
    0x3C: 0x003C, 0x3D: 0x003D, 0x3E: 0x003E, 0x3F: 0x003F,
    0x40: 0x0040, 0x41: 0x0041, 0x42: 0x0042, 0x43: 0x0043,
    0x44: 0x0044, 0x45: 0x0045, 0x46: 0x0046, 0x47: 0x0047,
    0x48: 0x0048, 0x49: 0x0049, 0x4A: 0x004A, 0x4B: 0x004B,
    0x4C: 0x004C, 0x4D: 0x004D, 0x4E: 0x004E, 0x4F: 0x004F,
    0x50: 0x0050, 0x51: 0x0051, 0x52: 0x0052, 0x53: 0x0053,
    0x54: 0x0054, 0x55: 0x0055, 0x56: 0x0056, 0x57: 0x0057,
    0x58: 0x0058, 0x59: 0x0059, 0x5A: 0x005A, 0x5B: 0x005B,
    0x5C: 0x005C, 0x5D: 0x005D, 0x5E: 0x2015, 0x5F: 0x005F,  # 0x5E is ― not ^
    0x60: 0x2016, 0x61: 0x0061, 0x62: 0x0062, 0x63: 0x0063,  # 0x60 is ║ not `
    0x64: 0x0064, 0x65: 0x0065, 0x66: 0x0066, 0x67: 0x0067,
    0x68: 0x0068, 0x69: 0x0069, 0x6A: 0x006A, 0x6B: 0x006B,
    0x6C: 0x006C, 0x6D: 0x006D, 0x6E: 0x006E, 0x6F: 0x006F,
    0x70: 0x0070, 0x71: 0x0071, 0x72: 0x0072, 0x73: 0x0073,
    0x74: 0x0074, 0x75: 0x0075, 0x76: 0x0076, 0x77: 0x0077,
    0x78: 0x0078, 0x79: 0x0079, 0x7A: 0x007A, 0x7B: 0x007B,
    0x7C: 0x007C, 0x7D: 0x007D, 0x7E: 0x203E, 0x7F: 0x007F,  # 0x7E is ¯ not ~
    0x80: 0x00E1, 0x81: 0x00E0, 0x82: 0x00E9, 0x83: 0x00E8,
    0x84: 0x00ED, 0x85: 0x00EC, 0x86: 0x00F3, 0x87: 0x00F2,
    0x88: 0x00FA, 0x89: 0x00F9, 0x8A: 0x00D1, 0x8B: 0x00C7,
    0x8C: 0x015E, 0x8D: 0x00DF, 0x8E: 0x00A1, 0x8F: 0x0132,
    0x90: 0x00E2, 0x91: 0x00E4, 0x92: 0x00EA, 0x93: 0x00EB,
    0x94: 0x00EE, 0x95: 0x00EF, 0x96: 0x00F4, 0x97: 0x00F6,
    0x98: 0x00FB, 0x99: 0x00FC, 0x9A: 0x00F1, 0x9B: 0x00E7,
    0x9C: 0x015F, 0x9D: 0x011F, 0x9E: 0x0131, 0x9F: 0x0133,
    0xA0: 0x00AA, 0xA1: 0x03B1, 0xA2: 0x00A9, 0xA3: 0x2030,
    0xA4: 0x011E, 0xA5: 0x011B, 0xA6: 0x0148, 0xA7: 0x0151,
    0xA8: 0x03C0, 0xA9: 0x20AC, 0xAA: 0x00A3, 0xAB: 0x0024,
    0xAC: 0x2190, 0xAD: 0x2191, 0xAE: 0x2192, 0xAF: 0x2193,
    0xB0: 0x00BA, 0xB1: 0x00B9, 0xB2: 0x00B2, 0xB3: 0x00B3,
    0xB4: 0x00B1, 0xB5: 0x0130, 0xB6: 0x0144, 0xB7: 0x0171,
    0xB8: 0x00B5, 0xB9: 0x00BF, 0xBA: 0x00F7, 0xBB: 0x00B0,
    0xBC: 0x00BC, 0xBD: 0x00BD, 0xBE: 0x00BE, 0xBF: 0x00A7,
    0xC0: 0x00C1, 0xC1: 0x00C0, 0xC2: 0x00C9, 0xC3: 0x00C8,
    0xC4: 0x00CD, 0xC5: 0x00CC, 0xC6: 0x00D3, 0xC7: 0x00D2,
    0xC8: 0x00DA, 0xC9: 0x00D9, 0xCA: 0x0158, 0xCB: 0x010C,
    0xCC: 0x0160, 0xCD: 0x017D, 0xCE: 0x00D0, 0xCF: 0x013F,
    0xD0: 0x00C2, 0xD1: 0x00C4, 0xD2: 0x00CA, 0xD3: 0x00CB,
    0xD4: 0x00CE, 0xD5: 0x00CF, 0xD6: 0x00D4, 0xD7: 0x00D6,
    0xD8: 0x00DB, 0xD9: 0x00DC, 0xDA: 0x0159, 0xDB: 0x010D,
    0xDC: 0x0161, 0xDD: 0x017E, 0xDE: 0x0111, 0xDF: 0x0140,
    0xE0: 0x00C3, 0xE1: 0x00C5, 0xE2: 0x00C6, 0xE3: 0x0152,
    0xE4: 0x0177, 0xE5: 0x00DD, 0xE6: 0x00D5, 0xE7: 0x00D8,
    0xE8: 0x00DE, 0xE9: 0x014A, 0xEA: 0x0154, 0xEB: 0x0106,
    0xEC: 0x015A, 0xED: 0x0179, 0xEE: 0x0166, 0xEF: 0x00F0,
    0xF0: 0x00E3, 0xF1: 0x00E5, 0xF2: 0x00E6, 0xF3: 0x0153,
    0xF4: 0x0175, 0xF5: 0x00FD, 0xF6: 0x00F5, 0xF7: 0x00F8,
    0xF8: 0x00FE, 0xF9: 0x014B, 0xFA: 0x0155, 0xFB: 0x0107,
    0xFC: 0x015B, 0xFD: 0x017A, 0xFE: 0x0167, 0xFF: 0x0000,
}

# Create reverse mapping: Unicode to RDS
UNICODE_TO_RDS = {v: k for k, v in RDS_TO_UNICODE.items() if v != 0x0000 or k == 0x00}
# Handle special case: $ (U+0024) should map to 0xAB in RDS, not be missing
UNICODE_TO_RDS[0x0024] = 0xAB  # Dollar sign at correct position

# RDS PTY List (Europe/Rest of World)
PTY_LIST_RDS = ["None", "News", "Current Affairs", "Information", "Sport", "Education", "Drama", "Culture", "Science", "Varied", "Pop Music", "Rock Music", "Easy Music", "Light Classical", "Serious Classical", "Other Music", "Weather", "Finance", "Children's", "Social Affairs", "Religion", "Phone-In", "Travel", "Leisure", "Jazz", "Country", "National Music", "Oldies", "Folk Music", "Documentary", "Alarm Test", "Alarm"]

# RBDS PTY List (North America)
PTY_LIST_RBDS = ["None", "News", "Information", "Sport", "Talk", "Rock", "Classic Rock", "Adult Hits", "Soft Rock", "Top 40", "Country", "Oldies", "Soft", "Nostalgia", "Jazz", "Classical", "R&B", "Soft R&B", "Language", "Religious Music", "Religious Talk", "Personality", "Public", "College", "Unassigned", "Unassigned", "Unassigned", "Unassigned", "Unassigned", "Weather", "Emergency Test", "Emergency"]

# Legacy alias for backwards compatibility
PTY_LIST = PTY_LIST_RDS

# --- RT+ CONTENT TYPES (EN 62106 / IEC 62106) ---
# Organized by logical hierarchy for better UX
RTPLUS_CONTENT_TYPES = {
    0: ("Dummy", "No content type", "system"),
    1: ("Title", "Item title", "music"),
    2: ("Album", "Album/CD name", "music"),
    3: ("Track", "Track number", "music"),
    4: ("Artist", "Artist name", "music"),
    5: ("Composition", "Composition name", "music"),
    6: ("Movement", "Movement name", "music"),
    7: ("Conductor", "Conductor", "music"),
    8: ("Composer", "Composer", "music"),
    9: ("Band", "Band/Orchestra", "music"),
    10: ("Comment", "Free text comment", "system"),
    11: ("Genre", "Genre", "music"),
    12: ("News", "News headlines", "news"),
    13: ("News.Local", "Local news", "news"),
    14: ("Stock", "Stock market", "news"),
    15: ("Sport", "Sport news", "news"),
    16: ("Lottery", "Lottery numbers", "news"),
    17: ("Horoscope", "Horoscope", "news"),
    18: ("Daily", "Daily diversion", "news"),
    19: ("Health", "Health tips", "news"),
    20: ("Event", "Event info", "news"),
    21: ("Scene", "Scene/Film info", "media"),
    22: ("Cinema", "Cinema info", "media"),
    23: ("TV", "TV info", "media"),
    24: ("DateTime", "Date/Time", "utility"),
    25: ("Weather", "Weather info", "utility"),
    26: ("Traffic", "Traffic info", "utility"),
    27: ("Alarm", "Alarm/Emergency", "utility"),
    28: ("Advert", "Advertisement", "utility"),
    29: ("URL", "Website URL", "utility"),
    30: ("Other", "Other info", "utility"),
    31: ("Stn.Short", "Station name short", "station"),
    32: ("Stn.Long", "Station name long", "station"),
    33: ("Prog.Now", "Current program", "station"),
    34: ("Prog.Next", "Next program", "station"),
    35: ("Prog.Part", "Program part", "station"),
    36: ("Host", "Host name", "station"),
    37: ("Editorial", "Editorial staff", "station"),
    38: ("Frequency", "Frequency info", "station"),
    39: ("Homepage", "Homepage URL", "station"),
    40: ("Subchannel", "Sub-channel", "station"),
    41: ("Phone.Hotline", "Hotline phone", "contact"),
    42: ("Phone.Studio", "Studio phone", "contact"),
    43: ("Phone.Other", "Other phone", "contact"),
    44: ("SMS.Studio", "Studio SMS", "contact"),
    45: ("SMS.Other", "Other SMS", "contact"),
    46: ("Email.Hotline", "Hotline email", "contact"),
    47: ("Email.Studio", "Studio email", "contact"),
    48: ("Email.Other", "Other email", "contact"),
    49: ("MMS.Phone", "MMS number", "contact"),
    50: ("Chat", "Chat", "contact"),
    51: ("Chat.Centre", "Chat centre", "contact"),
    52: ("Vote.Question", "Vote question", "contact"),
    53: ("Vote.Centre", "Vote centre", "contact"),
    54: ("RFU", "Reserved", "system"),
    55: ("RFU", "Reserved", "system"),
    56: ("RFU", "Reserved", "system"),
    57: ("RFU", "Reserved", "system"),
    58: ("RFU", "Reserved", "system"),
    59: ("Place", "Place/Location", "location"),
    60: ("Appointment", "Appointment", "location"),
    61: ("Identifier", "Identifier", "location"),
    62: ("Purchase", "Purchase info", "location"),
    63: ("GetData", "Get Data", "location"),
}

# RT+ Tag Categories for hierarchical organization
RTPLUS_CATEGORIES = {
    "music": {
        "name": "🎵 Music & Audio",
        "description": "Musical content identification",
        "color": "text-purple-400"
    },
    "news": {
        "name": "📰 News & Information",
        "description": "News, events, and informational content",
        "color": "text-blue-400"
    },
    "media": {
        "name": "📺 Media & Entertainment", 
        "description": "Film, TV, and entertainment content",
        "color": "text-red-400"
    },
    "station": {
        "name": "📻 Station Information",
        "description": "Radio station and program details",
        "color": "text-green-400"
    },
    "contact": {
        "name": "📞 Contact & Interaction",
        "description": "Communication and voting services",
        "color": "text-cyan-400"
    },
    "utility": {
        "name": "🔧 Utility & Services",
        "description": "General purpose and utility information",
        "color": "text-yellow-400"
    },
    "location": {
        "name": "📍 Location & Services",
        "description": "Location-based and commercial services",
        "color": "text-pink-400"
    },
    "system": {
        "name": "⚙️ System & Reserved",
        "description": "System types and reserved entries",
        "color": "text-gray-400"
    }
}

def get_rtplus_type_info(type_code):
    """Get RT+ type information including category."""
    if type_code not in RTPLUS_CONTENT_TYPES:
        return {"name": "Unknown", "description": "", "category": "system", "category_info": RTPLUS_CATEGORIES["system"]}
    
    type_data = RTPLUS_CONTENT_TYPES[type_code]
    category = type_data[2] if len(type_data) > 2 else "system"
    
    return {
        "name": type_data[0],
        "description": type_data[1],
        "category": category,
        "category_info": RTPLUS_CATEGORIES.get(category, RTPLUS_CATEGORIES["system"])
    }

app = Flask(__name__)
CONFIG_FILE = 'config.ini'  # Legacy - deprecated
DATASETS_FILE = os.path.join(os.path.dirname(__file__), 'datasets.json')
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp'}

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp'}

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_or_create_secret():
    """Get or create secret key from datasets.json."""
    try:
        if os.path.exists(DATASETS_FILE):
            with open(DATASETS_FILE, 'r') as f:
                data = json.load(f)
                if 'system' in data and 'secret_key' in data['system']:
                    return data['system']['secret_key']
    except:
        pass
    
    # Generate new secret
    secret = os.urandom(24).hex()
    
    # Save to datasets.json
    try:
        data = {}
        if os.path.exists(DATASETS_FILE):
            with open(DATASETS_FILE, 'r') as f:
                data = json.load(f)
        if 'system' not in data:
            data['system'] = {}
        data['system']['secret_key'] = secret
        with open(DATASETS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving secret: {e}")
    
    return secret

app.secret_key = os.environ.get("RDS_SECRET", get_or_create_secret())
# Restrict CORS to same origin only for security (prevents cross-site WebSocket attacks)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins=[])

auth_config = {"user": "admin", "pass": "pass"}

# --- DEFAULT STATE ---
default_state = {
    "running": False,
    # Audio
    "device_out_idx": 0, "device_in_idx": -1,
    "genlock": False, "passthrough": False,
    "pilot_level": 0.0, "rds_level": 4.5, "genlock_offset": 0.0,
    "output_channel": "both",  # Output routing: "both", "left", "right"

    # Basic
    "pi": "2FFF", "pty": 0, "rbds": False, "tp": 1, "ta": 0, "ms": 1,
    "di_stereo": 1, "di_head": 0, "di_comp": 1, "di_dyn": 0,
    "en_af": 1, "af_list": "87.6, 87.7", "af_method": "A", "af_pairs": "[]",
    "en_eon": 0, "eon_services": "[]",
    
    # Text
    "ps_dynamic": "RDSMASTR", "ps_centered": False, "ps_group_version": "0A",
    "rt_text": "RDS MASTER", "rt_manual_buffers": False, "rt_cycle_ab": False,
    "rt_a": "RDS MASTER", "rt_b": "Simple & Open Source RDS Encoder",
    "rt_cr": True, "rt_centered": False, "rt_disable_0d": False,
    "rt_mode": "2A", "rt_cycle": True, "rt_cycle_time": 5, "rt_active_buffer": 0,
    "rt_ab_cycle_count": 2,
    "rt_auto_ab": False,       # AUTO buffer mode: flip A/B only when file/text content changes

    # RT+
    "rt_plus_format_a": "{artist} - {title}",
    "rt_plus_format_b": "{artist} - {title}",
    "en_rt_plus": False,
    "rt_plus_mode": "format",  # "format" (legacy), "builder" (new modal), or "regex"
    "rt_plus_builder_a": "",   # JSON string with builder config
    "rt_plus_builder_b": "",   # JSON string with builder config
    "rt_plus_regex_rules_a": "[]",  # JSON array of regex rule dicts for buffer A
    "rt_plus_regex_rules_b": "[]",  # JSON array of regex rule dicts for buffer B

    # RT Messages (unified message list - replaces individual RT fields when used)
    "rt_messages": "[]",  # JSON array of message objects
    
    # Expert
    "ecc": "E3", "lic": "09", "tz_offset": 0.0, "en_ct": 1, "en_id": 1,
    "en_pin": 0, "pin_day": 0, "pin_hour": 0, "pin_minute": 0,
    "ps_long_32": f"RDS MASTER {VERSION}", "en_lps": 1, "lps_centered": False, "lps_cr": True,
    "ptyn": "RDSMASTR", "en_ptyn": 1, "ptyn_centered": False,
    "en_dab": 0, "dab_channel": "12C", "dab_eid": "2E01", "dab_mode": 1, "dab_es_flag": 0,
    "dab_sid": "0000", "dab_variant": 0,
    "custom_oda_list": "[]",  # JSON array of custom ODA configurations
    "custom_groups": "[]",  # JSON array of custom group data
    "dynamic_control_enabled": False,  # Enable dynamic RDS control from JSON
    "dynamic_control_rules": "[]",  # JSON array of dynamic control rules

    # RDS2
    "en_rds2": False,  # Enable RDS2 functionality
    "rds2_num_carriers": 3,  # Number of RDS2 carriers (1-3)
    "rds2_carrier1_level": 4.5,  # RDS2 carrier 1 (66.5 kHz) level %
    "rds2_carrier2_level": 4.5,  # RDS2 carrier 2 (71.25 kHz) level %
    "rds2_carrier3_level": 4.5,  # RDS2 carrier 3 (76 kHz) level %
    "rds2_logo_path": "",  # Path to station logo for RDS2 file transfer
    "rds2_logo_filename": "",  # Just the filename for display/serving

    # ARI (Autofahrer-Rundfunk-Information) - Traffic Radio System
    "en_ari": False,  # Enable ARI transmission
    "ari_region": "A",  # Region identifier (A-F) - determines BK frequency
    "ari_announcement": False,  # Announcement identifier (DK) manual state
    "ari_announcement_mode": "manual",  # "manual" or "rds_ta" (tied to TA+TP)
    "ari_bk_level": 60,  # BK modulation depth (% of carrier)
    "ari_dk_level": 30,  # DK modulation depth (% of carrier)

    # Settings
    "rds_freq": 57000,

    # Transparent Data Channels (TDC) - Type 5A/5B
    "en_tdc_5a": False,  # Enable TDC on 5A
    "en_tdc_5b": False,  # Enable TDC on 5B
    "tdc_5a_channel": 0,  # Channel number for 5A (0-31)
    "tdc_5b_channel": 0,  # Channel number for 5B (0-31)
    "tdc_5a_text": "",  # Custom text for 5A
    "tdc_5b_text": "",  # Custom text for 5B
    "tdc_5a_mode": "custom",  # Mode: "custom", "pc_status"
    "tdc_5b_mode": "custom",  # Mode: "custom", "pc_status"
    "tdc_pc_show_cpu": True,  # Show CPU usage in PC status
    "tdc_pc_show_temp": True,  # Show temperature in PC status
    "tdc_pc_show_ip": True,  # Show local IP in PC status
    "tdc_pc_show_ram": False,  # Show RAM usage in PC status
    "tdc_pc_show_uptime": False,  # Show system uptime in PC status

    # Scheduler
    "group_sequence": "0A 0A 2A 0A 0A 2A 0A",
    "scheduler_auto": True,

    # UECP server
    "uecp_enabled": False,
    "uecp_port": 4001,
    "uecp_host": "0.0.0.0",
    "uecp_psn": 0,   # Programme Service Number filter (0 = accept all)
    "uecp_dsn": 0,   # Dataset Number filter (0 = accept all)
    "uecp_ws_enabled": False,
    "uecp_ws_url": "ws://127.0.0.1/pacific",

    # Enhanced RadioText (eRT) - ODA Application
    "en_ert": False,  # Enable eRT transmission
    "ert_text": "RDSMaster RDS Encoder — ENHANCED RT (eRT)",  # eRT message text
    "ert_encoding": "utf8",  # "utf8" or "ucs2" encoding
    "ert_messages": "[]",  # JSON array of eRT message objects
    "ert_group_type": 13,  # Application group type for eRT data (11A recommended)
    "ert_direction": "ltr",  # Text direction: "ltr" (left-to-right) - always 0 per spec
    "en_ert_rtplus": False,  # Enable RT+ tagging for eRT
    "ert_rtplus_tags": "[]",  # JSON array of RT+ tag definitions for eRT
    "ert_rtplus_group_type": 6,  # Application group type for eRT RT+ (separate from eRT data group)
    "ert_source": "ert",  # Content source: "ert" = eRT message manager, "rt" = mirror RT message manager
}

state = default_state.copy()
resolved_cache = {"ps_dynamic": "", "ps_long_32": "", "rt_text": "", "rt_a": "", "rt_b": "", "ptyn": ""}

# Dynamic Control Overrides - parameters controlled remotely (takes priority over state)
dynamic_overrides = {}  # {parameter_name: override_value}

def get_effective_value(param):
    """Get effective value: override if exists, otherwise state value"""
    return dynamic_overrides.get(param, state.get(param))

# Global Monitor Data
monitor_data = {
    "ps": "OFF AIR",
    "rt": "",
    "ert": "",
    "en_ert": False,
    "ert_rtplus_info": "",
    "lps": "",
    "ptyn": "",
    "en_ptyn": False,
    "af": "",
    "pi": "0000",
    "pty_idx": 10,
    "rt_plus_info": "",
    "heartbeat": 0,
    "pilot_generated": True,
    "en_pin": 0,
    "pin_str": ""
}

# --- RT+ PARSER ---
import json

class RTPlusParser:
    @staticmethod
    def parse(text, fmt_str, centered=False, limit=64, builder_state=None):
        """
        Parse RT+ tags from text.

        Supports two modes:
        1. Format string mode (legacy): Uses {artist}/{title} placeholders
        2. Builder mode (new): Uses explicit builder_state with tag positions
        """
        tags = []
        if not text:
            return tags

        # Calculate centering offset
        offset = 0
        if centered and len(text) < limit:
            offset = (limit - len(text)) // 2

        # Builder mode: use explicit positions from builder state
        if builder_state:
            return RTPlusParser._parse_builder_mode(text, builder_state, offset, limit)

        # Legacy format string mode
        if not fmt_str:
            return tags
        return RTPlusParser._parse_format_mode(text, fmt_str, offset, limit)

    @staticmethod
    def _parse_format_mode(text, fmt_str, offset, limit):
        """Parse using format string pattern matching (legacy mode)."""
        tags = []

        pattern = re.escape(fmt_str)
        pattern = pattern.replace(r"\{artist\}", r"(?P<artist>.+)")
        pattern = pattern.replace(r"\{title\}", r"(?P<title>.+)")

        match = re.search(pattern, text)
        if match:
            for name, _ in match.groupdict().items():
                raw_start = match.start(name)
                length = len(match.group(name))

                real_start = raw_start + offset
                c_type = 1 if name == "title" else 4  # 1=Title, 4=Artist

                if real_start < limit and length > 0:
                    if real_start + length > limit:
                        length = limit - real_start
                    tags.append((c_type, real_start, length))

        return tags

    @staticmethod
    def _resolve_dynamic(text):
        r"""Resolve dynamic source patterns (\\r, \\R, \\w) in text."""
        if not text or "\\" not in text:
            return text
        result = parse_text_source(text)
        return result if result is not None else text

    @staticmethod
    def _parse_builder_mode(text, builder_state, offset, limit):
        """Parse using explicit builder state with position calculation."""
        tags = []

        try:
            if isinstance(builder_state, str):
                if not builder_state.strip():
                    return tags
                builder_state = json.loads(builder_state)
        except:
            return tags

        # Resolve dynamic sources in all text fields
        prefix = RTPlusParser._resolve_dynamic(builder_state.get('prefix', ''))
        tag1_type = int(builder_state.get('tag1_type', -1))
        tag1_text = RTPlusParser._resolve_dynamic(builder_state.get('tag1_text', ''))
        middle = RTPlusParser._resolve_dynamic(builder_state.get('middle', ''))
        tag2_type = int(builder_state.get('tag2_type', -1))
        tag2_text = RTPlusParser._resolve_dynamic(builder_state.get('tag2_text', ''))

        # Calculate Tag 1 position based on resolved text
        if tag1_text and tag1_type >= 0:
            tag1_start = len(prefix) + offset
            tag1_len = len(tag1_text)

            if tag1_start < limit and tag1_len > 0:
                if tag1_start + tag1_len > limit:
                    tag1_len = limit - tag1_start
                tags.append((tag1_type, tag1_start, tag1_len))

        # Calculate Tag 2 position based on resolved text
        if tag2_text and tag2_type >= 0:
            tag2_start = len(prefix) + len(tag1_text) + len(middle) + offset
            tag2_len = len(tag2_text)

            if tag2_start < limit and tag2_len > 0:
                if tag2_start + tag2_len > limit:
                    tag2_len = limit - tag2_start
                tags.append((tag2_type, tag2_start, tag2_len))

        return tags

    @staticmethod
    def parse_regex_rules(text, rules_json, offset=0, limit=64):
        """Apply regex rules in order; first match wins.

        Rule schema: {"pattern": str, "tag1_type": int, "tag2_type": int (optional, default -1)}

        - 0 capture groups: whole match span → tag1_type
        - 1+ capture groups: group(1) → tag1_type, group(2) → tag2_type (if present)

        Returns list of (content_type, start, length).
        """
        tags = []
        if not text:
            return tags
        try:
            rules = json.loads(rules_json) if isinstance(rules_json, str) else list(rules_json)
        except Exception:
            return tags
        for rule in rules:
            pattern = rule.get("pattern", "")
            if not pattern:
                continue
            t1 = int(rule.get("tag1_type", -1))
            t2 = int(rule.get("tag2_type", -1))
            try:
                m = re.search(pattern, text)
            except re.error:
                continue
            if not m:
                continue
            ng = len(m.groups())
            if ng == 0:
                s = m.start() + offset
                l = len(m.group(0))
                if t1 >= 0 and s < limit and l > 0:
                    tags.append((t1, s, min(l, limit - s)))
            else:
                if t1 >= 0 and m.group(1) is not None:
                    s = m.start(1) + offset
                    l = len(m.group(1))
                    if s < limit and l > 0:
                        tags.append((t1, s, min(l, limit - s)))
                if ng >= 2 and t2 >= 0 and m.group(2) is not None:
                    s = m.start(2) + offset
                    l = len(m.group(2))
                    if s < limit and l > 0:
                        tags.append((t2, s, min(l, limit - s)))
            break  # first match wins
        return tags

    @staticmethod
    def build_rt_from_builder(builder_state, resolve=True):
        """Construct RadioText string from builder state."""
        try:
            if isinstance(builder_state, str):
                if not builder_state.strip():
                    return ""
                builder_state = json.loads(builder_state)
        except:
            return ""

        # Resolve dynamic sources if requested
        if resolve:
            prefix = RTPlusParser._resolve_dynamic(builder_state.get('prefix', ''))
            tag1_text = RTPlusParser._resolve_dynamic(builder_state.get('tag1_text', ''))
            middle = RTPlusParser._resolve_dynamic(builder_state.get('middle', ''))
            tag2_text = RTPlusParser._resolve_dynamic(builder_state.get('tag2_text', ''))
            suffix = RTPlusParser._resolve_dynamic(builder_state.get('suffix', ''))
        else:
            prefix = builder_state.get('prefix', '')
            tag1_text = builder_state.get('tag1_text', '')
            middle = builder_state.get('middle', '')
            tag2_text = builder_state.get('tag2_text', '')
            suffix = builder_state.get('suffix', '')

        tag2_type = int(builder_state.get('tag2_type', -1))

        msg = prefix + tag1_text
        if tag2_type >= 0 and tag2_text:
            msg += middle + tag2_text
        msg += suffix

        return msg

# --- DEVICE DISCOVERY ---
def get_valid_devices():
    valid_inputs = []
    valid_outputs = []
    try:
        devs = sd.query_devices()
        apis = sd.query_hostapis()
        for i, d in enumerate(devs):
            api_name = apis[d['hostapi']]['name']
            if REQUIRE_HOSTAPI and REQUIRE_HOSTAPI not in api_name: continue
            try:
                if d['max_output_channels'] > 0:
                    sd.check_output_settings(device=i, samplerate=SAMPLE_RATE)
                    valid_outputs.append({'index': i, 'name': f"{d['name']} ({api_name})"})
                if d['max_input_channels'] > 0:
                    sd.check_input_settings(device=i, samplerate=SAMPLE_RATE)
                    valid_inputs.append({'index': i, 'name': f"{d['name']} ({api_name})"})
            except: continue
    except Exception as e: print(f"Device Error: {e}")
    return valid_inputs, valid_outputs

# --- CONFIG ---
def migrate_config_ini():
    """Migrate config.ini to datasets.json format, then delete config.ini."""
    global auth_config
    
    if not os.path.exists(CONFIG_FILE):
        return  # Nothing to migrate
    
    print("Migrating config.ini to datasets.json...")
    
    try:
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE)
        
        # Load existing datasets.json or create new structure
        data = {}
        if os.path.exists(DATASETS_FILE):
            with open(DATASETS_FILE, 'r') as f:
                data = json.load(f)
        
        if 'datasets' not in data:
            data['datasets'] = {}
        if 'system' not in data:
            data['system'] = {}
        if 'auth' not in data:
            data['auth'] = {}
        
        # Migrate RDS state to Dataset 1
        migrated_state = dict(default_state)
        auto_start_migrated = True  # Default value
        
        if 'RDS' in config:
            for k in migrated_state:
                if k in config['RDS']:
                    val = config['RDS'][k]
                    if isinstance(default_state[k], bool):
                        migrated_state[k] = (val == 'True')
                    elif isinstance(default_state[k], int) and not isinstance(default_state[k], bool):
                        migrated_state[k] = int(val)
                    elif isinstance(default_state[k], float):
                        migrated_state[k] = float(val)
                    else:
                        migrated_state[k] = val
            
            # Migrate auto_start if it exists in config.ini
            if 'auto_start' in config['RDS']:
                auto_start_migrated = (config['RDS']['auto_start'] == 'True')
        
        # Remove auto_start from migrated_state if it exists (it should be global, not per-dataset)
        migrated_state.pop('auto_start', None)
        
        # Create or update Dataset 1
        if '1' not in data['datasets']:
            data['datasets']['1'] = {'name': 'Dataset 1 (Migrated)', 'state': migrated_state}
        else:
            data['datasets']['1']['state'].update(migrated_state)
            # Also clean up auto_start from existing dataset state
            data['datasets']['1']['state'].pop('auto_start', None)
        
        if 'current' not in data:
            data['current'] = 1
        
        # Save auto_start as global setting (NOT per-dataset)
        data['auto_start'] = auto_start_migrated
        
        # Migrate AUTH
        if 'AUTH' in config:
            data['auth']['user'] = config['AUTH'].get('user', 'admin')
            data['auth']['pass'] = config['AUTH'].get('pass', 'admin')
            auth_config['user'] = data['auth']['user']
            auth_config['pass'] = data['auth']['pass']
        
        # Migrate SYSTEM (secret_key)
        if 'SYSTEM' in config and 'secret_key' in config['SYSTEM']:
            data['system']['secret_key'] = config['SYSTEM']['secret_key']
        
        # Save migrated data
        with open(DATASETS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        
        # Delete config.ini after successful migration
        os.remove(CONFIG_FILE)
        print(f"✓ Migrated config.ini to datasets.json and removed old file.")
        print(f"✓ auto_start set to: {auto_start_migrated}")
        
    except Exception as e:
        print(f"Error migrating config.ini: {e}")
        import traceback
        traceback.print_exc()

def load_config():
    """Load configuration from datasets.json."""
    global state, auth_config, current_dataset, auto_start, site_name, http_port
    
    # First, check if we need to migrate from config.ini
    migrate_config_ini()
    
    # Load from datasets.json
    auto_start_was_missing = False
    try:
        if os.path.exists(DATASETS_FILE):
            with open(DATASETS_FILE, 'r') as f:
                data = json.load(f)
                
                # Load current dataset
                current_dataset_str = str(data.get('current', 1))
                current_dataset = int(current_dataset_str)
                
                if 'datasets' in data and current_dataset_str in data['datasets']:
                    dataset_state = data['datasets'][current_dataset_str].get('state', {})
                    # Remove auto_start and http_port from state if they exist (they should only be at root level)
                    dataset_state.pop('auto_start', None)
                    dataset_state.pop('http_port', None)
                    state.update(dataset_state)
                    # Reset running state - encoder is never running at startup
                    state["running"] = False
                    print(f"Loaded state from Dataset {current_dataset_str} (device_out_idx={state.get('device_out_idx')})")
                
                # Load auth
                if 'auth' in data:
                    auth_config['user'] = data['auth'].get('user', auth_config['user'])
                    auth_config['pass'] = data['auth'].get('pass', auth_config['pass'])
                
                # Load auto_start global setting
                if 'auto_start' not in data:
                    auto_start_was_missing = True
                    print("⚠ auto_start missing from datasets.json, will add it...")
                auto_start = data.get('auto_start', True)

                # Load site_name global setting
                site_name = data.get('site_name', 'Secure Login')

                # Load http_port global setting
                http_port = data.get('http_port', 5000)
        else:
            print("datasets.json not found, using default state.")
    except Exception as e:
        print(f"Error loading config from datasets.json: {e}")
        import traceback
        traceback.print_exc()
    
    print("Config loaded from datasets.json.")
    migrate_rt_messages()

    # Initialize monitor_data from loaded state to prevent blank display on startup
    global monitor_data
    monitor_data["pi"] = state.get("pi", "0000")
    monitor_data["pty_idx"] = state.get("pty", 0)
    monitor_data["ps"] = state.get("ps_dynamic", "RDS PRO")
    monitor_data["rt"] = state.get("rt_text", "")
    
    # Fix datasets.json if auto_start was missing
    if auto_start_was_missing:
        # Don't reload datasets here, just save to add auto_start
        # Loading datasets again would overwrite the state we just loaded
        save_datasets()  # This will add auto_start to the root level
        print(f"✓ Added auto_start={auto_start} to datasets.json")

def migrate_rt_messages():
    """Migrate old RT config fields to new unified rt_messages structure."""
    global state
    try:
        existing = json.loads(state.get("rt_messages", "[]"))
        if existing:
            return  # Already has messages, don't migrate
    except:
        pass

    messages = []
    msg_id = 1

    def parse_legacy_text(text, buffer):
        """Parse legacy RT text with timing syntax into message entries."""
        nonlocal msg_id
        entries = []
        if not text:
            return entries

        # Check for timed syntax: "5s:Msg1 / 10s:Msg2"
        if re.match(r"\s*\d+s:", text):
            for part in re.split(r"\s*/\s*", text):
                m = re.match(r"\s*(\d+)s:(.*)", part)
                if m:
                    cycles = int(m.group(1))
                    content = m.group(2).strip()
                    if content:
                        entries.append({
                            "id": f"msg_{msg_id}",
                            "buffer": buffer,
                            "cycles": cycles,
                            "source_type": "manual",
                            "content": content,
                            "split_delimiter": " - ",
                            "rt_plus_enabled": False,
                            "rt_plus_tags": {"tag1_type": 4, "tag2_type": 1},
                            "enabled": True
                        })
                        msg_id += 1
        else:
            # Single message
            entries.append({
                "id": f"msg_{msg_id}",
                "buffer": buffer,
                "cycles": 2,
                "source_type": "manual",
                "content": text.strip(),
                "split_delimiter": " - ",
                "rt_plus_enabled": False,
                "rt_plus_tags": {"tag1_type": 4, "tag2_type": 1},
                "enabled": True
            })
            msg_id += 1
        return entries

    # Migrate based on mode
    if state.get("rt_manual_buffers"):
        # Manual buffer mode: migrate rt_a and rt_b
        if state.get("rt_a"):
            messages.extend(parse_legacy_text(state["rt_a"], "A"))
        if state.get("rt_b"):
            messages.extend(parse_legacy_text(state["rt_b"], "B"))
    else:
        # Auto mode: migrate rt_text
        if state.get("rt_text"):
            messages.extend(parse_legacy_text(state["rt_text"], "AB"))

    if messages:
        state["rt_messages"] = json.dumps(messages)
        print(f"Migrated {len(messages)} RT message(s) from legacy config.")

def _atomic_write_json(filepath, data):
    """Write JSON atomically: write to temp file then rename, so the original
    is never truncated unless the write fully succeeds."""
    dir_name = os.path.dirname(os.path.abspath(filepath))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        # Windows-friendly atomic replace with retry logic
        import platform
        if platform.system() == 'Windows':
            import time
            import stat
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    # On Windows, remove read-only attribute if it exists
                    if os.path.exists(filepath):
                        try:
                            # Remove read-only flag if set
                            os.chmod(filepath, stat.S_IWRITE | stat.S_IREAD)
                        except Exception:
                            pass

                        # Remove existing file
                        try:
                            os.remove(filepath)
                        except FileNotFoundError:
                            # File was already removed, that's fine
                            pass

                    # Rename temp file to final location
                    os.rename(tmp_path, filepath)
                    break
                except (PermissionError, FileExistsError, FileNotFoundError) as e:
                    if attempt < max_retries - 1:
                        # Wait progressively longer on each retry
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    else:
                        print(f"Failed to save after {max_retries} attempts: {e}")
                        raise
        else:
            # On Unix/Linux, os.replace is truly atomic
            os.replace(tmp_path, filepath)
    except Exception as e:
        print(f"Error in _atomic_write_json: {e}")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

def save_config():
    """Save configuration to datasets.json."""
    try:
        # Load existing data
        data = {}
        if os.path.exists(DATASETS_FILE):
            try:
                # Explicitly open, read, and close the file to avoid handle leaks
                f = open(DATASETS_FILE, 'r', encoding='utf-8')
                try:
                    content = f.read().strip()
                    if content:  # Only parse if file has content
                        data = json.loads(content)
                    else:
                        print("Warning: datasets.json is empty, using default structure")
                        data = {}
                finally:
                    f.close()  # Explicitly close the file handle
            except json.JSONDecodeError as je:
                print(f"Warning: datasets.json is corrupted ({je}), creating new structure")
                # Backup corrupted file
                import shutil
                backup_path = DATASETS_FILE + '.corrupted'
                try:
                    shutil.copy2(DATASETS_FILE, backup_path)
                    print(f"Corrupted file backed up to {backup_path}")
                except Exception as backup_err:
                    print(f"Could not create backup: {backup_err}")
                data = {}
        
        # Ensure structure exists
        if 'datasets' not in data:
            data['datasets'] = {}
        if 'auth' not in data:
            data['auth'] = {}
        
        # Get current dataset — use in-memory variable, NOT what's in the file
        # The file's 'current' may be stale (race with save_datasets, or deleted dataset)
        current = str(current_dataset)

        # Update current dataset state
        if current not in data['datasets']:
            data['datasets'][current] = {'name': f'Dataset {current}', 'state': {}}

        data['datasets'][current]['state'] = dict(state)

        # CRITICAL: Also update the in-memory datasets dictionary to prevent data bleeding
        # when switching between datasets
        if current in datasets:
            datasets[current]['state'] = dict(state)
        
        # Always save current auth credentials
        data['auth'] = {
            'user': auth_config.get('user', 'admin'),
            'pass': auth_config.get('pass', 'admin')
        }
        
        # Save global auto_start setting
        data['auto_start'] = auto_start

        # Save site_name
        data['site_name'] = site_name

        # Save http_port
        data['http_port'] = http_port

        # Validate JSON serializability before writing
        try:
            json.dumps(data)
        except (TypeError, ValueError) as e:
            print(f"Error: State contains non-serializable data: {e}")
            return
        
        # Save atomically (temp file + rename) so file is never left corrupted
        _atomic_write_json(DATASETS_FILE, data)
    except Exception as e:
        print(f"Error saving config: {e}")
        import traceback
        traceback.print_exc()

# --- DATASETS ---
# DATASETS_FILE already defined above near CONFIG_FILE
datasets = {}
current_dataset = 1
auto_start = True  # Global setting, not per-dataset
site_name = "Secure Login"  # Global setting for UI branding
http_port = 5000  # Global setting for HTTP port, not per-dataset

def load_datasets():
    global datasets, current_dataset
    try:
        if os.path.exists(DATASETS_FILE):
            with open(DATASETS_FILE, 'r') as f:
                data = json.load(f)
                datasets = data.get('datasets', {})
                # Clean up auto_start and http_port from all dataset states
                for ds_key in datasets:
                    if 'state' in datasets[ds_key]:
                        datasets[ds_key]['state'].pop('auto_start', None)
                        datasets[ds_key]['state'].pop('http_port', None)
                current_dataset = data.get('current', 1)
        else:
            datasets = {'1': {'name': 'Dataset 1', 'state': dict(state)}}
            current_dataset = 1
    except:
        datasets = {'1': {'name': 'Dataset 1', 'state': dict(state)}}
        current_dataset = 1

def save_datasets():
    """Save datasets along with auth and system settings."""
    global auto_start, http_port
    try:
        # Load existing data to preserve auth and system (best-effort — if file is
        # unreadable/corrupt we start fresh rather than aborting the whole save)
        data = {}
        if os.path.exists(DATASETS_FILE):
            try:
                with open(DATASETS_FILE, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
            except Exception as read_err:
                print(f"Warning: could not read {DATASETS_FILE} before save_datasets ({read_err}), continuing with empty base")
        
        # Sync live state into current dataset slot before writing
        if str(current_dataset) in datasets:
            datasets[str(current_dataset)]['state'] = dict(state)
        
        # Clean up: remove auto_start and http_port from all dataset states before saving
        datasets_clean = {}
        for ds_key, ds_value in datasets.items():
            datasets_clean[ds_key] = {'name': ds_value.get('name', f'Dataset {ds_key}')}
            if 'state' in ds_value:
                state_copy = dict(ds_value['state'])
                state_copy.pop('auto_start', None)  # Ensure auto_start never in state
                state_copy.pop('http_port', None)  # Ensure http_port never in state
                datasets_clean[ds_key]['state'] = state_copy
        
        # Update datasets and current
        data['datasets'] = datasets_clean
        data['current'] = current_dataset
        data['auto_start'] = auto_start  # Save global auto_start setting at root level
        data['http_port'] = http_port  # Save global http_port setting at root level

        # Always save current auth credentials
        data['auth'] = {'user': auth_config.get('user', 'admin'), 'pass': auth_config.get('pass', 'admin')}
        
        # Preserve system if it exists
        if 'system' not in data:
            data['system'] = {}
        
        # Validate JSON serializability before writing
        try:
            json.dumps(data)
        except (TypeError, ValueError) as e:
            print(f"Error: Dataset state contains non-serializable data: {e}")
            return

        # Save atomically (temp file + rename) so file is never left corrupted
        _atomic_write_json(DATASETS_FILE, data)
    except Exception as e:
        print(f"Error saving datasets: {e}")

def switch_dataset(dataset_num):
    global state, current_dataset, monitor_data
    dataset_num = str(dataset_num)
    if dataset_num in datasets:
        # Save current state to the CURRENT dataset before switching
        datasets[str(current_dataset)]['state'] = dict(state)

        # Switch to new dataset
        current_dataset = int(dataset_num)

        # Reset to default state first, then apply new dataset state
        state.clear()
        state.update(default_state.copy())
        new_state = datasets[dataset_num]['state'].copy()
        new_state.pop('auto_start', None)  # Ensure auto_start not in state
        new_state.pop('http_port', None)  # Ensure http_port not in state
        state.update(new_state)

        # Clear monitor_data to prevent showing stale values from previous dataset
        monitor_data.clear()
        monitor_data["pi"] = state.get("pi", "0000")
        monitor_data["pty_idx"] = state.get("pty", 0)
        monitor_data["ps"] = state.get("ps_dynamic", "")
        monitor_data["rt"] = state.get("rt_text", "")
        monitor_data["lps"] = state.get("ps_long_32", "")
        monitor_data["ptyn"] = state.get("ptyn", "")
        monitor_data["ert"] = ""
        monitor_data["rt_plus_info"] = ""
        monitor_data["ert_rtplus_info"] = ""
        monitor_data["heartbeat"] = int(time.time() * 1000)

        # Save datasets and current dataset info
        save_datasets()
        return True
    return False

def sig_abort(sig, frame):
    state["running"] = False
    save_config()
    os._exit(0)
signal.signal(signal.SIGINT, sig_abort)

# --- WORKERS ---

def convert_to_ebu_latin(text):
    """Convert UTF-8 text to RDS character encoding (IEC 62106-4:2018).

    RDS uses its own character set that differs from Latin-1/ISO-8859-1.
    Notable differences:
    - 0x24: ¤ (currency sign) not $ (dollar)
    - 0x5E: ― (horizontal bar) not ^ (caret)
    - 0x60: ║ (double vertical line) not ` (grave)
    - 0x7E: ¯ (overline) not ~ (tilde)
    """
    if not text:
        return ""
    result = []
    for char in text:
        code_point = ord(char)
        if code_point in UNICODE_TO_RDS:
            # Valid RDS character
            result.append(char)
        else:
            # Character not in RDS charset, replace with space
            result.append(' ')
    return ''.join(result)

def apply_text_trim(text, trim_parens=False, trim_brackets=False, trim_at_semicolon=False, max_len=0):
    """Apply optional text processing rules to clean up RT/eRT content.

    trim_at_semicolon example: 'Artist; Feat. Someone - Title' -> 'Artist - Title'
    It trims content after ';' within each field segment (split by ' - ').
    """
    if not text:
        return text
    if trim_parens:
        text = re.sub(r'\([^)]*\)', '', text)
    if trim_brackets:
        text = re.sub(r'\[[^\]]*\]', '', text)
    if trim_at_semicolon:
        # Split on ' - ' delimiters (keep delimiter at start of each following part),
        # trim each segment at its first ';', then rejoin.
        parts = re.split(r'(?= - )', text)
        cleaned = []
        for part in parts:
            if ';' in part:
                part = part[:part.index(';')].rstrip()
            cleaned.append(part)
        text = ''.join(cleaned)
    # Collapse multiple spaces
    text = re.sub(r'  +', ' ', text).strip()
    if max_len > 0 and len(text) > max_len:
        text = text[:max_len-3].rstrip() + '...'
    return text


def text_to_rds_bytes(text):
    """Convert Unicode text to RDS byte codes (IEC 62106-4:2018).

    Returns a bytes object where each byte is the RDS character code.
    Characters not in the RDS charset are replaced with space (0x20).
    """
    if not text:
        return b''
    result = []
    for char in text:
        code_point = ord(char)
        if code_point in UNICODE_TO_RDS:
            # Valid RDS character - get its RDS byte code
            rds_code = UNICODE_TO_RDS[code_point]
            result.append(rds_code)
        else:
            # Character not in RDS charset, replace with space
            result.append(0x20)
    return bytes(result)

def parse_text_source(text, cached_value=""):
    """Parse text with dynamic sources (file/URL patterns).

    Args:
        text: Text to parse (may contain \\r"file" or \\w"url" patterns)
        cached_value: Last successfully parsed value to return on error

    Returns:
        Parsed text, or None if parse failed and should keep current cache,
        or cached_value if parse failed and we need a safe fallback.
    """
    if not text: return ""
    try:
        if "\\" in text:
            # Track if any substitution failed
            failed = False
            def file_repl(m):
                nonlocal failed
                try:
                    # Use utf-8-sig to automatically strip BOM, then convert special chars
                    with open(m.group(1), 'r', encoding='utf-8-sig') as f:
                        content = f.read().strip()
                        return convert_to_ebu_latin(content)
                except Exception as e:
                    print(f"File read error for {m.group(1)}: {e} - using cached content")
                    failed = True
                    return ""  # Return empty on error
            def url_repl(m):
                nonlocal failed
                try:
                    with urllib.request.urlopen(m.group(1), timeout=2) as r:
                        content = r.read().decode('utf-8').strip()
                        return convert_to_ebu_latin(content)
                except Exception as e:
                    print(f"URL fetch error for {m.group(1)}: {e} - using cached content")
                    failed = True
                    return ""  # Return empty on error

            def clean_spaces(s):
                # Normalize any embedded newlines to spaces so RT doesn't break lines unexpectedly
                return s.replace('\r', ' ').replace('\n', ' ')

            t = re.sub(r'\\R"([^"]+)"', lambda m: clean_spaces(file_repl(m)).upper(), text)
            t = re.sub(r'\\r"([^"]+)"', lambda m: clean_spaces(file_repl(m)), t)
            t = re.sub(r'\\w"([^"]+)"', lambda m: clean_spaces(url_repl(m)), t)

            # If any source failed, return cached value to prevent garbled output
            # This prevents UTF-8 encoding errors when network drops
            if failed:
                return cached_value if cached_value else None
            return t
        return text
    except: return text

def text_updater_loop():
    while True:
        if state["running"]:
            for k in ["ps_dynamic", "ps_long_32", "rt_text", "rt_a", "rt_b", "ptyn"]:
                if k in state and "\\" in state[k]:
                    # Pass current cached value so parse_text_source can return it on error
                    result = parse_text_source(state[k], resolved_cache.get(k, ""))
                    if result is not None:  # Only update if parse succeeded
                        resolved_cache[k] = result
        time.sleep(4.0)

def monitor_pusher_loop():
    while True:
        monitor_data["heartbeat"] = int(time.time() * 1000)
        if state["running"]:
            monitor_data["af_list"] = state["af_list"]
            monitor_data["af_method"] = state.get("af_method", "A")
            monitor_data["af_pairs"] = state.get("af_pairs", "[]")
            monitor_data["pty_idx"] = get_effective_value("pty")
            monitor_data["pi"] = get_effective_value("pi")
            monitor_data["tp"] = get_effective_value("tp")
            monitor_data["ta"] = get_effective_value("ta")
            monitor_data["ms"] = get_effective_value("ms")
            monitor_data["di_stereo"] = state.get("di_stereo", 1)
            monitor_data["di_head"] = state.get("di_head", 0)
            monitor_data["di_comp"] = state.get("di_comp", 1)
            monitor_data["di_dyn"] = state.get("di_dyn", 0)
            monitor_data["rbds"] = state.get("rbds", False)
            
            # EON networks list
            try:
                eon_services = json.loads(state.get("eon_services", "[]")) if isinstance(state.get("eon_services", "[]"), str) else state.get("eon_services", [])
                monitor_data["eon_networks"] = [{"pi": svc.get("pi_on", ""), "ps": svc.get("ps", "")} for svc in eon_services] if eon_services else []
            except:
                monitor_data["eon_networks"] = []

            # PIN status
            en_pin = state.get("en_pin", 0)
            monitor_data["en_pin"] = en_pin
            if en_pin:
                pin_day = state.get("pin_day", 0)
                pin_hour = state.get("pin_hour", 0)
                pin_minute = state.get("pin_minute", 0)
                monitor_data["pin_str"] = f"Day {pin_day}, {pin_hour:02d}:{pin_minute:02d}"
            else:
                monitor_data["pin_str"] = ""
            
            # RDS2 status - only count as active if RDS2 is actually enabled
            rds2_enabled = state.get("en_rds2", False)
            
            # If RDS2 is disabled, send all zeros
            if not rds2_enabled:
                monitor_data["rds2_enabled"] = False
                monitor_data["rds2_carrier_count"] = 0
                monitor_data["rds2_logo_filename"] = ""
                monitor_data["rds2_carrier_levels"] = [0, 0, 0]
            else:
                # RDS2 is enabled - check carrier levels
                carrier_levels = [
                    state.get("rds2_carrier1_level", 0),
                    state.get("rds2_carrier2_level", 0),
                    state.get("rds2_carrier3_level", 0)
                ]
                carrier_count = sum(1 for level in carrier_levels if level > 0)
                
                monitor_data["rds2_enabled"] = carrier_count > 0
                monitor_data["rds2_carrier_count"] = carrier_count
                monitor_data["rds2_carrier_levels"] = carrier_levels
                
                # Get logo filename - extract from path if filename field is empty
                logo_filename = state.get("rds2_logo_filename", "")
                if not logo_filename and state.get("rds2_logo_path"):
                    logo_filename = os.path.basename(state.get("rds2_logo_path"))
                monitor_data["rds2_logo_filename"] = logo_filename

            # ARI status
            ari_enabled = state.get("en_ari", False)
            monitor_data["en_ari"] = ari_enabled
            if ari_enabled:
                ari_region = state.get("ari_region", "B")
                ari_region_freqs = {"A": 23.75, "B": 28.274, "C": 34.926, "D": 39.583, "E": 45.673, "F": 53.977}
                monitor_data["ari_region"] = ari_region
                monitor_data["ari_bk_freq"] = ari_region_freqs.get(ari_region, 28.274)

                # Determine effective announcement state (manual or auto)
                ari_announcement_effective = get_effective_value("ari_announcement")
                if state.get("ari_announcement_mode", "manual") == "rds_ta":
                    tp_eff = get_effective_value("tp")
                    ta_eff = get_effective_value("ta")
                    ari_announcement_effective = (tp_eff == 1) and (ta_eff == 1)
                monitor_data["ari_announcement"] = ari_announcement_effective
                monitor_data["ari_announcement_mode"] = state.get("ari_announcement_mode", "manual")
            else:
                monitor_data["ari_region"] = ""
                monitor_data["ari_bk_freq"] = 0
                monitor_data["ari_announcement"] = False
                monitor_data["ari_announcement_mode"] = "manual"

            # Pilot generation: disabled if pass-through is enabled and an input device is selected, or when genlock is active
            monitor_data["pilot_generated"] = not ((state.get("passthrough") and state.get("device_in_idx") != -1) or state.get("genlock"))
            monitor_data["en_ert"] = bool(state.get("en_ert", False))
            monitor_data["en_ptyn"] = bool(state.get("en_ptyn", False))
            # Fallback: if scheduler hasn't fired an eRT group yet, show the configured text
            if monitor_data["en_ert"] and not monitor_data.get("ert"):
                monitor_data["ert"] = state.get("ert_text", "")
            elif not monitor_data["en_ert"]:
                monitor_data["ert"] = ""
            socketio.emit('monitor', monitor_data)
        else:
             socketio.emit('monitor', {
                 "ps": "OFF AIR", "rt": "Encoder Stopped", "ert": "", "en_ert": False, "ert_rtplus_info": "",
                 "lps": "", "ptyn": "", "en_ptyn": False, "af_list": "", "af_method": "A", "af_pairs": "[]", "pty_idx": 0, "rt_plus_info": "", "pi": "----",
                 "heartbeat": monitor_data["heartbeat"],
                 "pilot_generated": False,
                 "tp": 0, "ta": 0, "ms": 0,
                 "di_stereo": 0, "di_head": 0, "di_comp": 0, "di_dyn": 0,
                 "rbds": False, "eon_networks": [],
                 "rds2_enabled": False, "rds2_carrier_count": 0, "rds2_logo_filename": "", "rds2_carrier_levels": [0, 0, 0],
                 "en_ari": False, "ari_region": "", "ari_bk_freq": 0, "ari_announcement": False, "ari_announcement_mode": "manual"
             })
        time.sleep(0.2)

# Dynamic Control Worker
def dynamic_control_loop():
    """Background worker that fetches JSON data and applies control rules."""
    import urllib.request
    import urllib.error

    rule_next_poll = {}  # Track next poll time for each rule

    while True:
        try:
            if not state.get("dynamic_control_enabled", False):
                # Clear all overrides when disabled - fall back to dataset values
                if dynamic_overrides:
                    # print(f"[DYNAMIC CONTROL] Clearing {len(dynamic_overrides)} overrides - disabled", flush=True)
                    dynamic_overrides.clear()
                time.sleep(5.0)
                continue

            # Parse rules from JSON
            try:
                rules = json.loads(state.get("dynamic_control_rules", "[]"))
            except Exception as e:
                rules = []

            # Clear overrides for parameters that no longer have active rules
            active_params = set()
            for rule in rules:
                if rule.get("enabled", True):
                    active_params.add(rule.get("rds_param", ""))

            # Remove overrides for inactive parameters
            for param in list(dynamic_overrides.keys()):
                if param not in active_params:
                    del dynamic_overrides[param]

            current_time = time.time()

            for idx, rule in enumerate(rules):
                if not rule.get("enabled", True):
                    continue

                rule_key = f"{idx}_{rule.get('url', '')}"
                next_poll_time = rule_next_poll.get(rule_key, 0)

                if current_time < next_poll_time:
                    continue

                # Update next poll time
                poll_interval = rule.get("poll_interval", 5)
                rule_next_poll[rule_key] = current_time + poll_interval

                try:
                    # Fetch JSON data
                    url = rule.get("url", "")
                    if not url:
                        # print(f"[DYNAMIC CONTROL] Rule {idx}: No URL configured", flush=True)
                        continue

                    # print(f"[DYNAMIC CONTROL] Rule {idx}: Fetching from {url}", flush=True)
                    req = urllib.request.Request(url)
                    req.add_header('User-Agent', 'RDS-Encoder-Dynamic-Control/1.0')

                    with urllib.request.urlopen(req, timeout=5) as response:
                        json_data = json.loads(response.read().decode('utf-8'))

                    # print(f"[DYNAMIC CONTROL] Rule {idx}: JSON fetched successfully", flush=True)

                    # Extract field value using dot notation path
                    field_path = rule.get("field_path", "")
                    value = json_data

                    for key in field_path.split('.'):
                        if isinstance(value, dict):
                            value = value.get(key)
                        else:
                            value = None
                            break

                    if value is None:
                        continue

                    # Apply mapping based on type
                    rds_param = rule.get("rds_param", "")
                    mapping_type = rule.get("mapping_type", "direct")
                    custom_mapping = rule.get("custom_mapping")

                    new_value = None

                    if mapping_type == "direct":
                        # Direct 0/1 → 0/1
                        new_value = int(value) if str(value).isdigit() else 0

                    elif mapping_type == "boolean":
                        # true/false → 1/0
                        new_value = 1 if value in [True, "true", "True", 1, "1"] else 0

                    elif mapping_type == "text_match":
                        # Custom text mapping
                        if custom_mapping and isinstance(custom_mapping, dict):
                            new_value = custom_mapping.get(str(value))

                    elif mapping_type == "passthrough":
                        # Pass through as-is (for PTYN, PI)
                        new_value = str(value)

                    elif mapping_type == "pty_name":
                        # Map PTY name to number
                        pty_map = {
                            "None": 0, "News": 1, "Current Affairs": 2, "Information": 3,
                            "Sport": 4, "Education": 5, "Drama": 6, "Culture": 7,
                            "Science": 8, "Varied": 9, "Pop Music": 10, "Rock Music": 11,
                            "Easy Listening": 12, "Light Classical": 13, "Serious Classical": 14,
                            "Other Music": 15, "Weather": 16, "Finance": 17, "Children's": 18,
                            "Social Affairs": 19, "Religion": 20, "Phone-In": 21, "Travel": 22,
                            "Leisure": 23, "Jazz": 24, "Country": 25, "National Music": 26,
                            "Oldies": 27, "Folk Music": 28, "Documentary": 29, "Alarm Test": 30, "Alarm": 31
                        }
                        if custom_mapping and isinstance(custom_mapping, dict):
                            # Use custom mapping first
                            new_value = custom_mapping.get(str(value))
                        else:
                            # Use standard PTY mapping
                            new_value = pty_map.get(str(value))

                    elif mapping_type == "pty_number":
                        # Direct PTY number
                        new_value = int(value) if str(value).isdigit() else 0

                    elif mapping_type == "conditional":
                        # Support multiple conditions: if value matches any condition, apply corresponding output
                        # Supports two formats:
                        # 1. New format with multiple conditions: {"conditions": [{"match": "X", "output": "Y"}, ...]}
                        # 2. Old format (backward compat): {"condition_value": "X", "output_value": "Y"}

                        conditions = rule.get("conditions", [])

                        # Backward compatibility: convert old single-condition format to new array format
                        if not conditions and "condition_value" in rule:
                            conditions = [{
                                "match": rule.get("condition_value", ""),
                                "output": rule.get("output_value", "")
                            }]

                        # Try to match against all conditions
                        matched = False
                        for condition in conditions:
                            condition_match = condition.get("match", "")
                            output_value = condition.get("output", "")

                            # Check if current value matches this condition
                            if str(value) == str(condition_match):
                                matched = True
                                # Condition met - apply output value with proper type conversion
                                if rds_param in ["ms", "tp", "ta"]:
                                    new_value = int(output_value) if str(output_value).isdigit() else 0
                                elif rds_param == "pty":
                                    new_value = max(0, min(31, int(output_value))) if str(output_value).isdigit() else 0
                                elif rds_param == "pi":
                                    # Validate hex format
                                    try:
                                        new_value = format(int(str(output_value), 16), '04X')
                                    except:
                                        continue
                                elif rds_param == "ptyn":
                                    # Truncate to 8 characters and apply EBU Latin
                                    new_value = convert_to_ebu_latin(str(output_value)[:8])
                                else:
                                    new_value = output_value
                                break  # Stop checking other conditions once matched

                        if not matched:
                            # No condition matched - remove override to fall back to default
                            if rds_param in dynamic_overrides:
                                del dynamic_overrides[rds_param]
                            continue  # Skip setting new_value so it doesn't get applied

                    elif mapping_type == "list_match":
                        # List of values that all produce the same output.
                        # Useful for mapping many show/program names to one PTY/MS value.
                        match_list = rule.get("match_list", [])
                        list_output = rule.get("list_output", "")
                        if match_list and list_output is not None and list_output != "":
                            str_value = str(value).strip().lower()
                            if str_value in [str(item).strip().lower() for item in match_list]:
                                new_value = list_output
                            else:
                                if rds_param in dynamic_overrides:
                                    del dynamic_overrides[rds_param]
                                continue

                    # Apply the new value to state
                    if new_value is not None and rds_param in state:
                        # Special handling for certain parameters
                        if rds_param in ["ms", "tp", "ta"]:
                            new_value = int(new_value) if isinstance(new_value, (int, float, bool)) else 0
                        elif rds_param == "pty":
                            new_value = max(0, min(31, int(new_value))) if isinstance(new_value, (int, float)) else 0
                        elif rds_param == "pi":
                            # Validate hex format
                            try:
                                new_value = format(int(str(new_value), 16), '04X')
                            except:
                                continue
                        elif rds_param == "ptyn":
                            # Truncate to 8 characters and apply EBU Latin
                            new_value = convert_to_ebu_latin(str(new_value)[:8])

                        # Store as override (don't modify state - state is the fallback)
                        dynamic_overrides[rds_param] = new_value

                except urllib.error.URLError as e:
                    # Network error - silently continue
                    pass
                except Exception as e:
                    # Other errors - silently continue
                    pass

            time.sleep(1.0)  # Check rules every second (actual polling is per-rule)

        except Exception as e:
            time.sleep(5.0)

threading.Thread(target=text_updater_loop, daemon=True).start()
threading.Thread(target=monitor_pusher_loop, daemon=True).start()
threading.Thread(target=dynamic_control_loop, daemon=True).start()

class Sanitize:
    @staticmethod
    def parse_bool(v):
        if isinstance(v, bool): return v
        if isinstance(v, str): return v.lower() in ('true', '1', 'yes', 'on')
        if isinstance(v, int): return v != 0
        return False

    @staticmethod
    def to_state(data):
        global state, http_port
        changed = False
        # Fields that should NOT be converted to EBU Latin (JSON data, mode flags, etc.)
        skip_ebu_fields = {'rt_plus_builder_a', 'rt_plus_builder_b', 'rt_plus_mode', 'rt_messages', 'eon_services', 'af_pairs', 'custom_groups', 'custom_oda_list', 'rt_plus_regex_rules_a', 'rt_plus_regex_rules_b', 'dynamic_control_rules', 'ps_long_32', 'tdc_5a_text', 'tdc_5b_text', 'uecp_host', 'uecp_ws_url', 'ert_text', 'ert_messages', 'ert_source'}

        # Handle system-wide settings that are global variables, not in state
        if 'http_port' in data:
            try:
                http_port = int(data['http_port'])
                changed = True
            except:
                pass

        for k, v in data.items():
            if k in state:
                try:
                    if isinstance(state[k], bool): state[k] = Sanitize.parse_bool(v)
                    elif isinstance(state[k], float): state[k] = float(v)
                    elif isinstance(state[k], int): state[k] = int(v)
                    elif k in skip_ebu_fields:
                        # Store as-is without EBU Latin conversion
                        # Guard against None/null being saved as the string "None"
                        if v is None or str(v) in ('None', 'null'):
                            if k == 'ert_messages': state[k] = '[]'
                            elif k == 'ert_text': state[k] = ''
                            else: state[k] = str(v) if v is not None else ''
                        else:
                            state[k] = str(v)
                    else:
                        # Enforce EBU Latin for text fields to ensure spec compliance
                        state[k] = convert_to_ebu_latin(str(v))
                    changed = True
                except: pass
        if changed: save_config()

# --- RDS CORE ---
class RDSHelper:
    @staticmethod
    def crc(data, offset):
        reg = int(data) << 10
        for i in range(16):
            if (reg >> (25)) & 1: reg ^= (G_POLY << 15)
            reg = (reg << 1) & 0x3FFFFFF
        return ((reg >> 16) & 0x3FF) ^ offset

    @staticmethod
    def get_group_bits(g_type, ver, b2_tail, b3_val, b4_val):
        try: pi_v = int(get_effective_value("pi"), 16)
        except: pi_v = 0x0000
        b1 = (pi_v << 10) | RDSHelper.crc(pi_v, OFFSETS['A'])
        b2_v = (int(g_type) << 12) | (int(ver) << 11) | (int(get_effective_value("tp")) << 10) | (int(get_effective_value("pty")) << 5) | (int(b2_tail) & 0x1F)
        b2 = (b2_v << 10) | RDSHelper.crc(b2_v, OFFSETS['B'])
        b3 = (int(b3_val) << 10) | RDSHelper.crc(b3_val, OFFSETS['Cp'] if ver else OFFSETS['C'])
        b4 = (int(b4_val) << 10) | RDSHelper.crc(b4_val, OFFSETS['D'])
        bits = []
        for b in [b1, b2, b3, b4]:
            for i in range(25, -1, -1): bits.append((b >> i) & 1)
        return bits
    
    @staticmethod
    def rds2_blocks_to_bits(block0, block1, block2, block3):
        """Convert 4 raw RDS2 blocks to 104 bits with CRC checksums (for RDS2 carriers)"""
        # Add CRC checkwords to each block using standard offsets
        b0 = (int(block0) << 10) | RDSHelper.crc(block0, OFFSETS['A'])
        b1 = (int(block1) << 10) | RDSHelper.crc(block1, OFFSETS['B'])
        b2 = (int(block2) << 10) | RDSHelper.crc(block2, OFFSETS['C'])
        b3 = (int(block3) << 10) | RDSHelper.crc(block3, OFFSETS['D'])
        
        # Convert to bit stream
        bits = []
        for b in [b0, b1, b2, b3]:
            for i in range(25, -1, -1):
                bits.append((b >> i) & 1)
        return bits

class PCStatusMonitor:
    """Helper class to gather PC status information for TDC"""
    _cache = {}  # Cache status values to prevent constant text changes
    _cache_time = {}  # Track when each value was last updated

    @staticmethod
    def get_cpu_usage():
        """Get CPU usage percentage (cached for 2 seconds)"""
        now = time.time()
        if 'cpu' not in PCStatusMonitor._cache_time or (now - PCStatusMonitor._cache_time['cpu']) > 2.0:
            try:
                import psutil
                # Use interval=None (non-blocking) to avoid stalling the scheduler thread.
                # psutil internally tracks the last reading, so calling this every 2s gives
                # accurate results without blocking.
                pct = psutil.cpu_percent(interval=None)
                # On the very first call psutil returns 0.0 as it has no previous sample.
                # Trigger a background sample so the next cached read is accurate.
                if pct == 0.0 and 'cpu' not in PCStatusMonitor._cache:
                    # Schedule a one-shot delayed re-read after 1 s
                    def _refresh():
                        try:
                            import psutil as _p, time as _t
                            _t.sleep(1.0)
                            v = _p.cpu_percent(interval=None)
                            PCStatusMonitor._cache['cpu'] = f"{v:.0f}%"
                            PCStatusMonitor._cache_time['cpu'] = _t.time()
                        except:
                            pass
                    import threading
                    threading.Thread(target=_refresh, daemon=True).start()
                PCStatusMonitor._cache['cpu'] = f"{pct:.0f}%"
                PCStatusMonitor._cache_time['cpu'] = now
            except:
                PCStatusMonitor._cache['cpu'] = "N/A"
                PCStatusMonitor._cache_time['cpu'] = now
        return PCStatusMonitor._cache.get('cpu', 'N/A')

    @staticmethod
    def get_cpu_temp():
        """Get CPU temperature (cached for 5 seconds)"""
        now = time.time()
        if 'temp' not in PCStatusMonitor._cache_time or (now - PCStatusMonitor._cache_time['temp']) > 5.0:
            try:
                import psutil
                temps = psutil.sensors_temperatures()
                if temps:
                    # Try common sensor names
                    for name in ['coretemp', 'cpu_thermal', 'k10temp', 'zenpower']:
                        if name in temps:
                            temp = temps[name][0].current
                            PCStatusMonitor._cache['temp'] = f"{temp:.0f}C"
                            PCStatusMonitor._cache_time['temp'] = now
                            return PCStatusMonitor._cache['temp']
                PCStatusMonitor._cache['temp'] = "N/A"
                PCStatusMonitor._cache_time['temp'] = now
            except:
                PCStatusMonitor._cache['temp'] = "N/A"
                PCStatusMonitor._cache_time['temp'] = now
        return PCStatusMonitor._cache.get('temp', 'N/A')

    @staticmethod
    def get_local_ip():
        """Get local IP address (cached for 30 seconds)"""
        now = time.time()
        if 'ip' not in PCStatusMonitor._cache_time or (now - PCStatusMonitor._cache_time['ip']) > 30.0:
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.1)
                try:
                    s.connect(('10.255.255.255', 1))
                    ip = s.getsockname()[0]
                except:
                    ip = '127.0.0.1'
                finally:
                    s.close()
                PCStatusMonitor._cache['ip'] = ip
                PCStatusMonitor._cache_time['ip'] = now
            except:
                PCStatusMonitor._cache['ip'] = "N/A"
                PCStatusMonitor._cache_time['ip'] = now
        return PCStatusMonitor._cache.get('ip', 'N/A')

    @staticmethod
    def get_hostname():
        """Get system hostname"""
        if 'hostname' not in PCStatusMonitor._cache:
            try:
                import socket
                PCStatusMonitor._cache['hostname'] = socket.gethostname()
            except:
                PCStatusMonitor._cache['hostname'] = "N/A"
        return PCStatusMonitor._cache['hostname']

    @staticmethod
    def get_ram_usage():
        """Get RAM usage percentage (cached for 5 seconds)"""
        now = time.time()
        if 'ram' not in PCStatusMonitor._cache_time or (now - PCStatusMonitor._cache_time['ram']) > 5.0:
            try:
                import psutil
                vm = psutil.virtual_memory()
                PCStatusMonitor._cache['ram'] = f"{vm.percent:.0f}%"
                PCStatusMonitor._cache_time['ram'] = now
            except:
                PCStatusMonitor._cache['ram'] = "N/A"
                PCStatusMonitor._cache_time['ram'] = now
        return PCStatusMonitor._cache.get('ram', 'N/A')

    @staticmethod
    def get_uptime():
        """Get system uptime as a formatted string (cached for 30 seconds)"""
        now = time.time()
        if 'uptime' not in PCStatusMonitor._cache_time or (now - PCStatusMonitor._cache_time['uptime']) > 30.0:
            try:
                import psutil
                boot_ts = psutil.boot_time()
                secs = int(time.time() - boot_ts)
                days, rem = divmod(secs, 86400)
                hours, rem = divmod(rem, 3600)
                mins = rem // 60
                if days > 0:
                    uptime_str = f"{days}d {hours}h {mins}m"
                else:
                    uptime_str = f"{hours}h {mins}m"
                PCStatusMonitor._cache['uptime'] = uptime_str
                PCStatusMonitor._cache_time['uptime'] = now
            except:
                PCStatusMonitor._cache['uptime'] = "N/A"
                PCStatusMonitor._cache_time['uptime'] = now
        return PCStatusMonitor._cache.get('uptime', 'N/A')

    @staticmethod
    def format_pc_status():
        """Format PC status string based on enabled options"""
        parts = []

        # Always add RDS-MASTER branding with version
        parts.append(f"RDS-MASTER {VERSION}")

        # Add system info
        if state.get("tdc_pc_show_cpu", True):
            parts.append(f"CPU: {PCStatusMonitor.get_cpu_usage()}")
        if state.get("tdc_pc_show_temp", True):
            temp = PCStatusMonitor.get_cpu_temp()
            if temp != "N/A":
                parts.append(f"Temp: {temp}")
        if state.get("tdc_pc_show_ram", False):
            parts.append(f"RAM: {PCStatusMonitor.get_ram_usage()}")
        if state.get("tdc_pc_show_uptime", False):
            parts.append(f"Up: {PCStatusMonitor.get_uptime()}")
        if state.get("tdc_pc_show_ip", True):
            parts.append(f"IP: {PCStatusMonitor.get_local_ip()}")

        return " | ".join(parts) if parts else f"RDS-MASTER {VERSION}"

class RDSScheduler:
    def __init__(self):
        self.ps_ptr, self.rt_ptr, self.ptyn_ptr, self.lps_ptr, self.af_ptr = 0, 0, 0, 0, 0
        self.start_time = time.time()
        self.ct_min_lock = -1
        self.last_rt_content = ""
        self.last_rt_text_content = ""
        self.rt_ab_flag = 0
        self.rt_ab_cycles = 0  # number of full RT transmissions before toggling in cycle-ab mode
        self.last_rt_buf = 0   # track last A/B buffer used to reset pointer on change
        self.rt_sequence, self.rt_seq_idx = [(10, "")], 0
        self.rt_seq_start_time = time.time()
        self.last_ps_content = ""
        self.ps_sequence, self.ps_seq_idx = [(10, state.get("ps_dynamic", "RDS_PRO ").ljust(8)[:8])], 0
        self.ps_seq_start_time = time.time()
        self.burst_counter = 0
        self.last_lps_content = ""
        self.lps_sequence, self.lps_seq_idx = [(10, "")], 0
        self.lps_seq_start_time = time.time()
        self.last_ptyn_content = ""
        self.ptyn_sequence, self.ptyn_seq_idx = [(10, "")], 0
        self.ptyn_seq_start_time = time.time()
        self.schedule_ptr = 0

        self.rt_plus_toggle = 0
        self.rt_plus_tags = []
        self.last_rt_clean = ""

        self.dab_last_sent = time.time()
        self.group_3a_toggle = 0  # Toggle between DAB ODA and RT+ ODA on Group 3A
        self.schedule_gen_counter = 0  # Counter to half 3A frequency
        self.custom_group_indices = {}  # Track current index for each custom group type (key: "typeVer", value: index)

        # New unified RT message system
        self.rt_msg_idx = 0
        self.rt_msg_cycle_count = 0  # Track completed cycles for current message
        
        # Enhanced RadioText (eRT) state
        self.ert_ptr = 0  # Current segment pointer (5-bit, 0-31)
        self.last_ert_content = ""  # Track content changes
        self.ert_bytes = b""  # Cached encoded eRT bytes
        self.ert_msg_index = 0  # Current eRT message index
        self.ert_msg_cycle_count = 0  # Track completed cycles for current eRT message
        self.last_ert_messages_sig = ""  # Track changes to eRT message list
        # eRT RT+ (separate group, AID: 0x4BD8)
        self.ert_rt_plus_tags = []     # [(type, byte_start, byte_len), ...]
        self.ert_rt_plus_toggle = 0    # Toggle bit for eRT RT+ group
        self._ert_rt_plus_tags_sig = ""  # Change-detection key for toggle
        self._ert_content_new = False  # True while new content hasn't completed one full TX cycle
        self._ert_content_cache = {}   # Async-updated cache: {msg_id: content_str}
        self._ert_fetch_last = {}      # {msg_id: timestamp} of last successful fetch
        self._ert_fetch_pending = set() # msg_ids currently being fetched in background
        self._ert_rtplus_needs_clear = 0  # Injected 6A clear-group counter on content change
        # Start background thread that keeps file/URL eRT content up to date
        threading.Thread(target=self._ert_content_fetch_loop, daemon=True).start()

        # AF Method B transmission cache
        self.af_b_transmissions = []
        self.af_b_ptr = 0       # separate pointer - not shared with Method A af_ptr
        self._af_b_cache_key = ""  # combined key: method|af_pairs_json|af_list
        self.rt_msg_cache = {}  # Cache resolved content per message id
        self.last_rt_messages_sig = ""  # Track changes to message list
        self.ert_msg_last_good = {}  # Last successfully resolved eRT content per message id

        # Cache parsed custom groups to avoid json.loads() on every next() call
        self._custom_groups_cache = []
        self._custom_groups_cache_str = None

    def get_text(self, key):
        val = get_effective_value(key) if key in ["ptyn"] else state.get(key, "")
        result = resolved_cache.get(key, val) if "\\" in val else val

        # Safety fallback: if result still contains URL/file patterns (shouldn't happen
        # but protects against UTF-8 errors), strip them to prevent garbled output
        if "\\" in result and ("\\r\"" in result or "\\w\"" in result or "\\R\"" in result):
            # Strip any remaining pattern syntax as last resort
            result = re.sub(r'\\[rRw]"[^"]+"', '', result).strip()
            if not result:
                # If stripping left nothing, return cached value or empty
                result = resolved_cache.get(key, "")

        # Time placeholders: \HR\, \MN\, \S\ for dynamic time in PS/text
        if "\\" in result:
            now = datetime.now()
            result = result.replace("\\HR\\", f"{now.hour:02d}")
            result = result.replace("\\MN\\", f"{now.minute:02d}")
            result = result.replace("\\S\\", f"{now.second:02d}")

        return result

    def _get_custom_groups(self):
        """Return parsed custom groups list, re-parsing only when the JSON string changes."""
        raw = state.get("custom_groups", "[]")
        if raw != self._custom_groups_cache_str:
            try:
                self._custom_groups_cache_str = raw
                self._custom_groups_cache = json.loads(raw) if isinstance(raw, str) else list(raw)
            except Exception:
                self._custom_groups_cache = []
        return self._custom_groups_cache

    def get_rt_messages(self):
        """Get enabled RT messages from the unified message list."""
        try:
            messages = json.loads(state.get("rt_messages", "[]"))
            return [m for m in messages if m.get("enabled", True)]
        except:
            return []

    def resolve_msg_content(self, msg):
        """Resolve dynamic content for a message (file/URL/JSON sources)."""
        content = msg.get("content", "")
        source_type = msg.get("source_type", "manual")
        prefix = msg.get("prefix", "")
        suffix = msg.get("suffix", "")

        resolved = ""
        if source_type == "manual":
            # Check for inline dynamic patterns
            if "\\" in content:
                resolved = parse_text_source(content) or content
            else:
                resolved = content
        elif source_type == "file":
            # File path - read content
            try:
                with open(content, 'r', encoding='utf-8-sig', errors='replace') as f:
                    resolved = f.read().strip()
            except:
                resolved = ""
        elif source_type == "url":
            # URL - fetch content
            try:
                req = urllib.request.Request(content, headers={'User-Agent': 'RDS-Encoder/1.0'})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resolved = resp.read().decode('utf-8', errors='replace').strip()
            except:
                resolved = ""
        elif source_type == "json":
            # JSON source - fetch and extract fields
            try:
                url = content
                field1_path = msg.get("json_field1", "")
                field2_path = msg.get("json_field2", "")
                delimiter = msg.get("split_delimiter", " - ")
                hide_if_blank = msg.get("json_hide_if_blank", False)

                req = urllib.request.Request(url, headers={'User-Agent': 'RDS-Encoder/1.0'})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    response_bytes = resp.read()
                    try:
                        response_text = response_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        response_text = response_bytes.decode('latin-1')
                    json_data = json.loads(response_text)

                # Extract values by path
                def get_value_by_path(obj, path):
                    if not path:
                        return ''
                    parts = path.replace('[0]', '').split('.')
                    current = obj
                    for part in parts:
                        if isinstance(current, list) and len(current) > 0:
                            current = current[0]
                        if isinstance(current, dict) and part in current:
                            current = current[part]
                        else:
                            return ''
                    if current is None:
                        return ''
                    elif isinstance(current, str):
                        return current
                    else:
                        return str(current)

                field1_value = get_value_by_path(json_data, field1_path)
                field2_value = get_value_by_path(json_data, field2_path) if field2_path else ''

                # Build message
                if hide_if_blank and not field1_value:
                    # If Field 1 is blank and hide_if_blank is enabled, only show Field 2
                    resolved = field2_value
                else:
                    resolved = field1_value
                    if field2_value:
                        resolved += delimiter + field2_value
            except Exception:
                resolved = ""
        else:
            resolved = content

        # Apply prefix/suffix for file/URL sources
        if source_type in ("file", "url") and resolved:
            resolved = prefix + resolved + suffix

        # Apply optional text trim
        if resolved and any([msg.get('trim_parens'), msg.get('trim_brackets'),
                              msg.get('trim_at_semicolon'), msg.get('trim_max_len')]):
            resolved = apply_text_trim(
                resolved,
                trim_parens=msg.get('trim_parens', False),
                trim_brackets=msg.get('trim_brackets', False),
                trim_at_semicolon=msg.get('trim_at_semicolon', False),
                max_len=64 if msg.get('trim_max_len') else 0
            )

        return resolved

    def get_current_rt_message(self):
        """Get the current RT message based on cycle count and buffer state."""
        messages = self.get_rt_messages()
        if not messages:
            return None, 0, ""

        # Check if message list changed
        msg_sig = state.get("rt_messages", "[]")
        if msg_sig != self.last_rt_messages_sig:
            self.last_rt_messages_sig = msg_sig
            self.rt_msg_idx = 0
            self.rt_msg_cycle_count = 0
            self.rt_msg_cache = {}
            self.rt_ab_flag = 1 - self.rt_ab_flag  # Toggle on change

        # Get current message
        current_msg = messages[self.rt_msg_idx % len(messages)]

        # Determine buffer
        buffer_setting = current_msg.get("buffer", "AB")
        msg_id = current_msg.get("id", "")

        if buffer_setting == "AUTO":
            # AUTO: refresh content every 4 s (same cadence as text_updater_loop).
            # resolve_msg_content() opens the file directly, so we must NOT call it
            # on every scheduler tick (~11 /s) — that would cause GIL-holding disk I/O
            # in the real-time path and produce blank groups.
            if not hasattr(self, 'rt_msg_auto_content'):
                self.rt_msg_auto_content = {}
            if not hasattr(self, 'rt_msg_auto_refresh'):
                self.rt_msg_auto_refresh = {}
            now = time.time()
            if msg_id not in self.rt_msg_cache or \
                    now - self.rt_msg_auto_refresh.get(msg_id, 0) >= 4.0:
                new_content = self.resolve_msg_content(current_msg)
                self.rt_msg_cache[msg_id] = new_content
                self.rt_msg_auto_refresh[msg_id] = now
                prev = self.rt_msg_auto_content.get(msg_id)
                if prev is None:
                    self.rt_msg_auto_content[msg_id] = new_content  # seed, start on A
                elif new_content != prev:
                    self.rt_msg_auto_content[msg_id] = new_content
                    self.rt_ab_flag = 1 - self.rt_ab_flag  # flip on content change
            resolved_content = self.rt_msg_cache[msg_id]
            buf = self.rt_ab_flag
        else:
            if buffer_setting == "A":
                buf = 0
            elif buffer_setting == "B":
                buf = 1
            else:  # "AB"
                buf = self.rt_ab_flag

            # Resolve content (with caching for performance during message duration)
            if msg_id not in self.rt_msg_cache:
                self.rt_msg_cache[msg_id] = self.resolve_msg_content(current_msg)
            resolved_content = self.rt_msg_cache[msg_id]

        # If content is empty, skip to next message immediately
        if not resolved_content or not resolved_content.strip():
            self.advance_to_next_rt_message()
            # Retry with next message (limit recursion to prevent infinite loop)
            if len(messages) > 1:
                return self.get_current_rt_message()
            else:
                # Only one message and it's empty - return empty
                return current_msg, buf, ""

        return current_msg, buf, resolved_content

    def advance_to_next_rt_message(self, toggle_buffer=True):
        """Advance to the next RT message and reset cycle count."""
        messages = self.get_rt_messages()
        if not messages:
            return

        self.rt_msg_idx = (self.rt_msg_idx + 1) % len(messages)
        self.rt_msg_cycle_count = 0

        # Clear cache for new message to refresh file/URL content
        current_msg = messages[self.rt_msg_idx % len(messages)]
        msg_id = current_msg.get("id", "")
        if msg_id in self.rt_msg_cache:
            del self.rt_msg_cache[msg_id]

        # Toggle buffer when advancing to next message to prevent overwriting
        # Controlled by toggle_buffer parameter - AB messages don't toggle here since they already toggled
        if toggle_buffer:
            self.rt_ab_flag = 1 - self.rt_ab_flag

    def apply_tagging_policies_to_tags(self, content, tags, policies, offset, limit):
        """Apply tagging policies to modify or override RT+ tags."""
        import re
        
        for policy in policies:
            if not policy.get("enabled", True):
                continue
            
            policy_type = policy.get("type", "default")
            settings = policy.get("settings", {})
            
            if policy_type == "default":
                # Default policy: override tag types and apply split pattern
                tag1_type = int(settings.get("tag1_type", -1))
                tag2_type = int(settings.get("tag2_type", -1))
                split_pattern = settings.get("split_pattern", " - ")
                prefix = settings.get("prefix", "")
                suffix = settings.get("suffix", "")
                
                # Clear existing tags and recalculate with new types
                tags = []
                
                # Apply prefix/suffix to content for calculation
                working_content = prefix + content + suffix
                
                if split_pattern and split_pattern in content:
                    parts = content.split(split_pattern, 1)
                    if len(parts) >= 1 and tag1_type >= 0:
                        tag1_start = offset + len(prefix)
                        tag1_len = min(len(parts[0]), limit - tag1_start)
                        if tag1_start < limit and tag1_len > 0:
                            tags.append((tag1_type, tag1_start, tag1_len))
                    
                    if len(parts) >= 2 and tag2_type >= 0:
                        tag2_start = offset + len(prefix) + len(parts[0]) + len(split_pattern)
                        tag2_len = min(len(parts[1]), limit - tag2_start)
                        if tag2_start < limit and tag2_len > 0:
                            tags.append((tag2_type, tag2_start, tag2_len))
                else:
                    # No split, tag entire content
                    if tag1_type >= 0 and content:
                        tag1_start = offset + len(prefix)
                        tag1_len = min(len(content), limit - tag1_start)
                        if tag1_start < limit and tag1_len > 0:
                            tags.append((tag1_type, tag1_start, tag1_len))
            
            elif policy_type == "sub":
                # Sub-tagging policy: check trigger and condition, then apply tag
                trigger_type = settings.get("trigger_type", "none")
                trigger_pattern = settings.get("trigger_pattern", "")
                clear_on_match = settings.get("clear_on_match", True)  # Clear existing tags when this policy matches

                # Check trigger condition first
                if trigger_type and trigger_type != "none" and trigger_pattern:
                    trigger_matches = False
                    try:
                        if trigger_type == "contains":
                            trigger_matches = trigger_pattern.lower() in content.lower()
                        elif trigger_type == "starts_with":
                            trigger_matches = content.lower().startswith(trigger_pattern.lower())
                        elif trigger_type == "ends_with":
                            trigger_matches = content.lower().endswith(trigger_pattern.lower())
                        elif trigger_type == "equals":
                            trigger_matches = content.lower() == trigger_pattern.lower()
                        elif trigger_type == "regex":
                            trigger_matches = bool(re.search(trigger_pattern, content, re.IGNORECASE))
                    except:
                        pass

                    if not trigger_matches:
                        continue  # Skip this policy if trigger doesn't match

                    # If trigger matched and clear_on_match is True, clear existing tags
                    if trigger_matches and clear_on_match:
                        tags = []
                
                # Check main condition
                condition = settings.get("condition", "starts_with")
                pattern = settings.get("pattern", "")
                action = settings.get("action", "tag_all")
                tag_type = int(settings.get("tag_type", -1))
                strip_pattern = settings.get("strip_pattern", False)
                skip_tagging = settings.get("skip_tagging", False)  # If True, don't add any tags when matched

                # If no pattern, skip this policy
                if not pattern:
                    continue

                # If skip_tagging is enabled and tag_type is -1, that's valid (means "clear tags and don't add any")
                if not skip_tagging and tag_type < 0:
                    continue

                matches = False
                try:
                    if condition == "contains":
                        matches = pattern.lower() in content.lower()
                    elif condition == "starts_with":
                        matches = content.lower().startswith(pattern.lower())
                    elif condition == "ends_with":
                        matches = content.lower().endswith(pattern.lower())
                    elif condition == "equals":
                        matches = content.lower() == pattern.lower()
                    elif condition == "regex":
                        matches = bool(re.search(pattern, content, re.IGNORECASE))
                except:
                    pass

                if matches:
                    # If skip_tagging is True, clear tags and don't add any new ones
                    if skip_tagging:
                        tags = []
                        break  # No more processing needed
                    # Calculate tag position based on action
                    tag_start = offset
                    tag_content = content
                    
                    if action == "tag_after":
                        idx = content.lower().find(pattern.lower())
                        if idx != -1:
                            tag_start = offset + idx + len(pattern)
                            tag_content = content[idx + len(pattern):]
                    elif action == "tag_before":
                        idx = content.lower().find(pattern.lower())
                        if idx != -1:
                            tag_start = offset
                            tag_content = content[:idx]
                    elif action == "tag_match":
                        idx = content.lower().find(pattern.lower())
                        if idx != -1:
                            tag_start = offset + idx
                            tag_content = content[idx:idx + len(pattern)]
                    # else: tag_all uses full content
                    
                    if strip_pattern and action != "tag_match":
                        tag_content = tag_content.replace(pattern, "")
                    
                    tag_len = min(len(tag_content), limit - tag_start)
                    if tag_start < limit and tag_len > 0:
                        # Add tag to the list (allow multiple sub-tagging policies to apply)
                        tags.append((tag_type, tag_start, tag_len))
                        # RT+ supports up to 2 tags, so stop after 2
                        if len(tags) >= 2:
                            break
        
        return tags

    def get_ert_plus_tags_for_message(self, msg, raw_content, formatted_content, encoding='utf-8'):
        """Calculate eRT+ tags (in byte positions) for a message based on its configuration.

        Args:
            msg: The message configuration
            raw_content: Content WITHOUT prefix/suffix (used for tag calculation)
            formatted_content: Content WITH prefix/suffix (used for byte position conversion)
            encoding: 'utf-8' or 'ucs2'

        Returns:
            List of (tag_type, byte_start, byte_len) tuples
        """
        if not msg.get("rt_plus_enabled"):
            return []

        # For eRT+, we don't have centering/padding, so offset is always 0 in character space
        char_offset = 0
        # Limit is effectively unlimited for tagging purposes (128 bytes max for eRT)
        char_limit = 128 if encoding in ('utf-8', 'utf8') else 63  # 128 UTF-8 bytes or 126 UCS-2 bytes = 63 chars

        # First, calculate tags in character positions using RAW content
        # The apply_tagging_policies_to_tags function expects raw content and applies prefix/suffix internally
        char_tags = []

        # Apply tagging policies (handles both "default" and "sub" policies)
        if msg.get("tagging_policies"):
            try:
                policies = json.loads(msg.get("tagging_policies", "[]"))
                char_tags = self.apply_tagging_policies_to_tags(raw_content, char_tags, policies, char_offset, char_limit)
            except Exception as e:
                print(f"[eRT RT+] Error applying tagging policies: {e}", flush=True)

        # Convert character positions to byte positions using FORMATTED content
        # The character positions from apply_tagging_policies_to_tags already account for prefix/suffix
        byte_tags = []
        enc_name = 'utf-8' if encoding in ('utf-8', 'utf8') else 'utf-16-be'
        for tag_type, char_start, char_len in char_tags:
            # Calculate byte offset for the start position in the formatted content
            prefix_text = formatted_content[:char_start]
            byte_start = len(prefix_text.encode(enc_name))

            # Calculate byte length for the tag content
            tag_text = formatted_content[char_start:char_start + char_len]
            byte_len = len(tag_text.encode(enc_name))

            if byte_len > 0:
                byte_tags.append((tag_type, byte_start, byte_len))

        return byte_tags

    def get_rt_plus_tags_for_message(self, msg, resolved_content, limit):
        """Calculate RT+ tags for a message based on its configuration."""
        if not msg.get("rt_plus_enabled"):
            return []

        # Calculate centering offset (shared by all modes below)
        offset = 0
        if state.get("rt_centered") and len(resolved_content) < limit:
            offset = (limit - len(resolved_content)) // 2

        # Regex rules mode: highest priority
        if msg.get("rt_plus_mode") == "regex":
            return RTPlusParser.parse_regex_rules(
                resolved_content, msg.get("rt_plus_regex_rules", "[]"), offset, limit)

        tags = []
        tag_config = msg.get("rt_plus_tags", {})
        tag1_type = int(tag_config.get("tag1_type", -1))
        tag2_type = int(tag_config.get("tag2_type", -1))

        # Check if manual builder mode (has tag1_text field)
        if msg.get("tag1_text"):
            # Manual builder mode: use explicit positions
            prefix = msg.get("prefix", "")
            tag1_text = msg.get("tag1_text", "")
            middle = msg.get("middle", " - ")
            tag2_text = msg.get("tag2_text", "")

            # Tag 1 position
            if tag1_text and tag1_type >= 0:
                tag1_start = offset + len(prefix)
                tag1_len = min(len(tag1_text), limit - tag1_start)
                if tag1_start < limit and tag1_len > 0:
                    tags.append((tag1_type, tag1_start, tag1_len))

            # Tag 2 position
            if tag2_text and tag2_type >= 0:
                tag2_start = offset + len(prefix) + len(tag1_text) + len(middle)
                tag2_len = min(len(tag2_text), limit - tag2_start)
                if tag2_start < limit and tag2_len > 0:
                    tags.append((tag2_type, tag2_start, tag2_len))
        elif msg.get("source_type") == "json":
            # JSON mode: calculate tag positions from field mapping
            field1_path = msg.get("json_field1", "")
            field2_path = msg.get("json_field2", "")
            delimiter = msg.get("split_delimiter", " - ")
            hide_if_blank = msg.get("json_hide_if_blank", False)
            
            # Need to extract field values from resolved content to calculate positions
            # The resolved_content is already "field1 + delimiter + field2" or just field1/field2
            
            # Try to fetch JSON and extract fields to calculate actual positions
            try:
                url = msg.get("content", "")
                if url and field1_path:
                    import urllib.request
                    req = urllib.request.Request(url, headers={'User-Agent': 'RDS-Encoder/1.0'})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        response_bytes = resp.read()
                        try:
                            response_text = response_bytes.decode('utf-8')
                        except UnicodeDecodeError:
                            response_text = response_bytes.decode('latin-1')
                        json_data = json.loads(response_text)
                    
                    # Extract values by path
                    def get_value_by_path(obj, path):
                        if not path:
                            return ''
                        parts = path.replace('[0]', '').split('.')
                        current = obj
                        for part in parts:
                            if isinstance(current, list) and len(current) > 0:
                                current = current[0]
                            if isinstance(current, dict) and part in current:
                                current = current[part]
                            else:
                                return ''
                        if current is None:
                            return ''
                        elif isinstance(current, str):
                            return current
                        else:
                            return str(current)
                    
                    field1_value = get_value_by_path(json_data, field1_path)
                    field2_value = get_value_by_path(json_data, field2_path) if field2_path else ''
                    
                    # Calculate tag positions
                    if hide_if_blank and not field1_value:
                        # Only field2 is shown
                        if field2_value and tag2_type >= 0:
                            tag_start = offset
                            tag_len = min(len(field2_value), limit - tag_start)
                            if tag_start < limit and tag_len > 0:
                                tags.append((tag2_type, tag_start, tag_len))
                    else:
                        # Field1 + delimiter + field2 or just field1
                        if field1_value and tag1_type >= 0:
                            tag1_start = offset
                            tag1_len = min(len(field1_value), limit - tag1_start)
                            if tag1_start < limit and tag1_len > 0:
                                tags.append((tag1_type, tag1_start, tag1_len))
                        
                        if field2_value and tag2_type >= 0:
                            tag2_start = offset + len(field1_value) + len(delimiter)
                            tag2_len = min(len(field2_value), limit - tag2_start)
                            if tag2_start < limit and tag2_len > 0:
                                tags.append((tag2_type, tag2_start, tag2_len))
            except Exception as e:
                print(f"[RDS] Error extracting JSON fields for RT+ tags: {e}", flush=True)
        else:
            # Auto mode: split by delimiter (file/URL sources)
            # For file/URL, we need to account for prefix and suffix in the resolved_content
            prefix = msg.get("prefix", "")
            suffix = msg.get("suffix", "")
            delimiter = msg.get("split_delimiter", " - ")

            # Find where the actual content starts (after prefix) and ends (before suffix)
            content_without_prefix = resolved_content[len(prefix):] if prefix and resolved_content.startswith(prefix) else resolved_content
            # Remove suffix from the end if present
            if suffix and content_without_prefix.endswith(suffix):
                content_without_prefix_or_suffix = content_without_prefix[:-len(suffix)]
            else:
                content_without_prefix_or_suffix = content_without_prefix

            if delimiter and delimiter in content_without_prefix_or_suffix:
                parts = content_without_prefix_or_suffix.split(delimiter, 1)
                # Tag positions include the prefix offset
                tag1_start = offset + len(prefix)
                if len(parts) >= 1 and tag1_type >= 0:
                    tag1_len = min(len(parts[0]), limit - tag1_start)
                    if tag1_start < limit and tag1_len > 0:
                        tags.append((tag1_type, tag1_start, tag1_len))
                if len(parts) >= 2 and tag2_type >= 0:
                    tag2_start = offset + len(prefix) + len(parts[0]) + len(delimiter)
                    # Tag2 length excludes the suffix
                    tag2_len = min(len(parts[1]), limit - tag2_start)
                    if tag2_start < limit and tag2_len > 0:
                        tags.append((tag2_type, tag2_start, tag2_len))
            else:
                # No delimiter found - treat content (without prefix/suffix) as tag1
                if tag1_type >= 0 and content_without_prefix_or_suffix:
                    tag1_start = offset + len(prefix)
                    tag1_len = min(len(content_without_prefix_or_suffix), limit - tag1_start)
                    if tag1_start < limit and tag1_len > 0:
                        tags.append((tag1_type, tag1_start, tag1_len))

        # Apply tagging policies (for JSON, manual, and file/URL modes)
        if msg.get("tagging_policies"):
            try:
                policies = json.loads(msg.get("tagging_policies", "[]"))
                tags = self.apply_tagging_policies_to_tags(resolved_content, tags, policies, offset, limit)
            except Exception as e:
                print(f"[RDS] Error applying tagging policies: {e}", flush=True)

        return tags
    
    def get_ert_control_bits(self):
        """Generate eRT control bits for Group 3A message field (Block 4)."""
        # Bit allocation per IEC 62106-6 Figure C.1:
        # b0: UCS-2/UTF-8 marker (0=UCS-2, 1=UTF-8)
        # b1: Text direction (0=left-to-right, always 0 per spec)
        # b2-b5: Reserved (0)
        # b6-b15: Reserved future use (0)
        
        encoding = state.get("ert_encoding", "utf8")
        encoding_bit = 1 if encoding == "utf8" else 0
        direction_bit = 0  # Always left-to-right per spec
        
        return (encoding_bit << 0) | (direction_bit << 1)  # Other bits remain 0
    
    def encode_ert_text(self, text):
        """Encode eRT text as UTF-8 or UCS-2 bytes."""
        if not text:
            return b"\x0D"  # Just carriage return for empty text
            
        encoding = state.get("ert_encoding", "utf8")
        
        try:
            if encoding == "utf8":
                # UTF-8 encoding
                encoded = text.encode('utf-8')
            else:
                # UCS-2 encoding (big-endian)
                encoded = text.encode('utf-16-be')
            
            # Limit to 127 bytes to leave room for CR terminator
            if len(encoded) > 127:
                if encoding == "utf8":
                    # For UTF-8, we need to be careful not to split multi-byte characters
                    # Truncate safely by decoding back and re-encoding
                    truncated = encoded[:127].decode('utf-8', errors='ignore')
                    encoded = truncated.encode('utf-8')
                else:
                    # For UCS-2, each character is 2 bytes, so truncate to even boundary
                    encoded = encoded[:126]  # 126 bytes = 63 characters
            
            # Add carriage return terminator
            encoded += b"\x0D"
            
            return encoded
            
        except UnicodeError as e:
            print(f"eRT encoding error: {e}")
            return b"eRT encoding error\x0D"
    
    def generate_ert_group(self, g_ver):
        """Generate eRT application group (similar to RT but with 5-bit segment addressing)."""
        # Only version A supported for eRT
        if g_ver != 0:
            return RDSHelper.get_group_bits(0, 0, 0, 0, 0)  # Fallback

        # Get current eRT text - use effective text from message management
        ert_text = self.get_effective_ert_text()

        # CRITICAL: Don't switch content mid-transmission (applies to ALL sources)
        # If we're mid-transmission (ert_ptr != 0) and content changed, finish current message first
        # This prevents:
        # 1. Breaking RDS groups when URL/file changes (Bug #1)
        # 2. Sending garbled text when network drops mid-transmission (Bug #2)
        if self.ert_ptr != 0 and ert_text != self.last_ert_content:
            # Mid-transmission and content changed - continue with cached content
            # Only switch to new content after current transmission completes (ert_ptr wraps to 0)
            ert_text = self.last_ert_content

        monitor_data["ert"] = ert_text

        def _ert_tag_cfg_from_policies(msg):
            """Extract tag config from the first enabled default policy in tagging_policies."""
            try:
                policies = json.loads(msg.get("tagging_policies", "[]") or "[]")
                for p in policies:
                    if p.get("enabled", True) and p.get("type") == "default":
                        s = p.get("settings", {})
                        return (int(s.get("tag1_type", -1)),
                                int(s.get("tag2_type", -1)),
                                s.get("split_pattern", " - "),
                                s.get("prefix", ""),
                                s.get("suffix", ""))
            except Exception:
                pass
            return (-1, -1, " - ", "", "")

        # Compute eRT RT+ tag info for dashboard display
        if state.get("en_ert_rtplus") and state.get("en_ert"):
            try:
                ert_src = state.get("ert_source", "ert")
                if ert_src == "rt":
                    monitor_data["ert_rtplus_info"] = monitor_data.get("rt_plus_info", "")
                else:
                    messages = self.get_ert_messages()
                    current_msg = self.get_current_ert_message() if messages else None
                    if current_msg and current_msg.get("rt_plus_enabled", False):
                        # Get RAW content for tag calculation
                        raw_content = self.resolve_ert_msg_content(current_msg)
                        # Get tags using the unified method (returns byte positions)
                        encoding = state.get("ert_encoding", "utf8")
                        byte_tags = self.get_ert_plus_tags_for_message(current_msg, raw_content, ert_text, encoding)

                        # Convert byte positions back to character positions for display
                        enc_name = 'utf-8' if encoding in ('utf-8', 'utf8') else 'utf-16-be'
                        tag_strs = []
                        for tag_type, byte_start, byte_len in byte_tags:
                            # Find character position from byte position
                            char_pos = 0
                            byte_count = 0
                            for char_pos, char in enumerate(ert_text):
                                if byte_count >= byte_start:
                                    break
                                byte_count += len(char.encode(enc_name))

                            # Find character length from byte length
                            char_len = 0
                            byte_counted = 0
                            for i in range(char_pos, len(ert_text)):
                                if byte_counted >= byte_len:
                                    break
                                byte_counted += len(ert_text[i].encode(enc_name))
                                char_len += 1

                            tag_content = ert_text[char_pos:char_pos + char_len]
                            type_name = RTPLUS_CONTENT_TYPES.get(tag_type, ("Unknown",))[0]
                            tag_strs.append(f"{type_name}: {tag_content.strip()}")

                        monitor_data["ert_rtplus_info"] = " | ".join(tag_strs) if tag_strs else ""
                    else:
                        monitor_data["ert_rtplus_info"] = ""
            except Exception as e:
                print(f"[eRT RT+] display info error: {e}")
                monitor_data["ert_rtplus_info"] = ""
        else:
            monitor_data["ert_rtplus_info"] = ""

        # Compute byte-level RT+ tag positions for the dedicated eRT RT+ group
        if state.get("en_ert_rtplus") and state.get("en_ert"):
            try:
                ert_source = state.get("ert_source", "ert")
                if ert_source == "rt":
                    new_tags = list(self.rt_plus_tags) if hasattr(self, 'rt_plus_tags') else []
                    tags_sig = f"{ert_text}|rt|{new_tags}"
                    if tags_sig != self._ert_rt_plus_tags_sig:
                        self._ert_rt_plus_tags_sig = tags_sig
                        self.ert_rt_plus_toggle = 1 - self.ert_rt_plus_toggle
                    self.ert_rt_plus_tags = new_tags
                else:
                    messages = self.get_ert_messages()
                    current_msg = self.get_current_ert_message() if messages else None
                    if current_msg and current_msg.get("rt_plus_enabled", False):
                        # Get RAW content (before prefix/suffix) for tag calculation
                        raw_content = self.resolve_ert_msg_content(current_msg)
                        # Use the new unified tag calculation that handles all policy types
                        # Pass both raw content (for tag calculation) and formatted text (for byte conversion)
                        encoding = state.get("ert_encoding", "utf8")
                        new_tags = self.get_ert_plus_tags_for_message(current_msg, raw_content, ert_text, encoding)
                        tags_sig = f"{ert_text}|ert|{new_tags}"
                        if tags_sig != self._ert_rt_plus_tags_sig:
                            self._ert_rt_plus_tags_sig = tags_sig
                            self.ert_rt_plus_toggle = 1 - self.ert_rt_plus_toggle
                        self.ert_rt_plus_tags = new_tags
                    else:
                        self.ert_rt_plus_tags = []
            except Exception as e:
                print(f"[eRT RT+] tag computation error: {e}")
                self.ert_rt_plus_tags = []
        else:
            self.ert_rt_plus_tags = []

        if ert_text != self.last_ert_content:
            self.last_ert_content = ert_text
            self.ert_bytes = self.encode_ert_text(ert_text)
            self.ert_ptr = 0  # Reset pointer on text change
            self._ert_content_new = True  # Suppress RT+ tags until first full cycle sent
            if state.get("en_ert_rtplus"):
                self._ert_rtplus_needs_clear = 6  # Inject 6 immediate 6A clears via next()
        
        # Calculate how many segments we actually need (like RT termination)
        message_length = len(self.ert_bytes)
        segments_needed = (message_length + 3) // 4  # Round up to nearest 4-byte boundary
        segments_needed = max(1, segments_needed)  # At least 1 segment
        
        # If we've transmitted all segments, reset to beginning like RT
        if self.ert_ptr >= segments_needed:
            self.ert_ptr = 0
        
        # Calculate segment address (5-bit, 0-31) 
        segment_addr = self.ert_ptr
        
        # Get 4 bytes for this segment
        byte_offset = segment_addr * 4
        if byte_offset < message_length:
            # Extract bytes, padding short segments with CR
            b3_bytes = [0x0D, 0x0D]  # Default CR padding
            b4_bytes = [0x0D, 0x0D]
            
            # Fill with actual message bytes
            for i in range(4):
                if byte_offset + i < message_length:
                    if i < 2:
                        b3_bytes[i] = self.ert_bytes[byte_offset + i]
                    else:
                        b4_bytes[i - 2] = self.ert_bytes[byte_offset + i]
            
            b3 = (b3_bytes[0] << 8) | b3_bytes[1]
            b4 = (b4_bytes[0] << 8) | b4_bytes[1]
        else:
            # This shouldn't happen with proper segment limit
            b3 = 0x0D0D
            b4 = 0x0D0D
        
        # Advance pointer for next transmission
        self.ert_ptr = (self.ert_ptr + 1) % segments_needed

        # A full pass just completed — count it as one cycle
        if self.ert_ptr == 0:
            self._ert_content_new = False  # First full cycle done; decoder now has the complete text
            self.ert_msg_cycle_count += 1
            current_msg_for_cycle = self.get_current_ert_message()
            if current_msg_for_cycle:
                cycles_required = current_msg_for_cycle.get("cycles", 1)
                if self.ert_msg_cycle_count >= cycles_required:
                    self.advance_to_next_ert_message()

        # Block 2: Group type, version, TP, PTY, segment address (5-bit)
        ert_group_type = state.get("ert_group_type", 13)
        tail = (get_effective_value("tp") << 10) | (get_effective_value("pty") << 5) | segment_addr
        
        return RDSHelper.get_group_bits(ert_group_type, 0, tail, b3, b4)

    # Enhanced RadioText (eRT) Message Management
    def get_ert_messages(self):
        """Get enabled eRT messages from the unified message list."""
        try:
            messages = json.loads(state.get("ert_messages", "[]"))
            return [m for m in messages if m.get("enabled", True)]
        except:
            return []

    def get_current_ert_message(self):
        """Get the current active eRT message."""
        messages = self.get_ert_messages()
        if not messages:
            return None
        
        if self.ert_msg_index >= len(messages):
            self.ert_msg_index = 0
            
        return messages[self.ert_msg_index] if messages else None

    def advance_to_next_ert_message(self):
        """Advance to the next eRT message and reset cycles."""
        messages = self.get_ert_messages()
        if not messages:
            return
            
        # Check if current message signature changed (message list updated)
        msg_sig = state.get("ert_messages", "[]")
        if msg_sig != self.last_ert_messages_sig:
            self.last_ert_messages_sig = msg_sig
            self.ert_msg_index = 0  # Reset to first message
            self.ert_msg_cycle_count = 0
            return

        # Move to next message
        self.ert_msg_index = (self.ert_msg_index + 1) % len(messages)
        self.ert_msg_cycle_count = 0  # Reset cycle count for new message

    def _ert_content_fetch_loop(self):
        """Background daemon: refreshes file/URL eRT content every 5 s.
        The hot path (resolve_ert_msg_content) only reads from the cache
        and never blocks on I/O."""
        import urllib.request as _ur
        while state.get("running", True):
            try:
                messages = json.loads(state.get("ert_messages", "[]"))
            except Exception:
                messages = []
            for msg in messages:
                if not msg.get("enabled", True):
                    continue
                source_type = msg.get("source_type", "manual")
                if source_type not in ("file", "url"):
                    continue
                msg_id = msg.get("id", "")
                content = msg.get("content", "")
                now = time.time()
                # Only re-fetch if 5 s have elapsed since last successful fetch
                if now - self._ert_fetch_last.get(msg_id, 0) < 5.0:
                    continue
                if msg_id in self._ert_fetch_pending:
                    continue
                self._ert_fetch_pending.add(msg_id)
                try:
                    if source_type == "file":
                        with open(content, "r", encoding="utf-8") as f:
                            result = f.read().strip()
                    else:  # url
                        req = _ur.Request(content, headers={"User-Agent": "RDS-Encoder/1.0"})
                        with _ur.urlopen(req, timeout=5) as resp:
                            result = resp.read().decode("utf-8", errors="replace").strip()
                    self._ert_content_cache[msg_id] = result
                    self.ert_msg_last_good[msg_id] = result
                    self._ert_fetch_last[msg_id] = now
                except Exception as e:
                    print(f"[eRT] background fetch error ({source_type}): {e}")
                    self._ert_fetch_last[msg_id] = now  # back off for 5 s before retrying
                finally:
                    self._ert_fetch_pending.discard(msg_id)
            time.sleep(1.0)  # Check each message once per second (fetches only when TTL expired)

    def resolve_ert_msg_content(self, msg):
        """Return eRT message content from the in-memory cache.
        Always non-blocking. The _ert_content_fetch_loop daemon keeps the
        cache fresh in the background."""
        if not msg:
            return ""
        msg_id = msg.get("id", "")
        source_type = msg.get("source_type", "manual")
        content = msg.get("content", "")
        if source_type == "manual":
            result = content
        else:
            # file / url: return last-good cached value (never block)
            result = self._ert_content_cache.get(msg_id) or self.ert_msg_last_good.get(msg_id, "")

        # Apply optional text trim
        if result and any([msg.get('trim_parens'), msg.get('trim_brackets'),
                           msg.get('trim_at_semicolon'), msg.get('trim_max_len')]):
            result = apply_text_trim(
                result,
                trim_parens=msg.get('trim_parens', False),
                trim_brackets=msg.get('trim_brackets', False),
                trim_at_semicolon=msg.get('trim_at_semicolon', False),
                max_len=128 if msg.get('trim_max_len') else 0
            )
        return result

    def get_effective_ert_text(self):
        """Get the effective eRT text based on message management."""
        # If source is "rt", mirror the currently active RT text.
        # IMPORTANT: do NOT call get_current_rt_message() here — that function
        # has side effects (flips rt_ab_flag, mutates rt_msg_cache, etc.) which
        # would corrupt Group 2A / Group 11A RT+ when fired from the eRT path.
        # monitor_data["rt"] is already kept current by the Group 2A handler
        # with no re-entrancy risk.
        ert_source = state.get("ert_source", "ert")
        if ert_source == "rt":
            raw = monitor_data.get("rt", "")
            return raw.rstrip('\r').rstrip()  # strip padding/CR added by RT encoder

        messages = self.get_ert_messages()
        if messages:
            current_msg = self.get_current_ert_message()
            if current_msg:
                resolved_content = self.resolve_ert_msg_content(current_msg)
                # Apply RT+ formatting if enabled for this message
                if current_msg.get("rt_plus_enabled", False):
                    resolved_content = self.apply_ert_rtplus_formatting(current_msg, resolved_content)
                return resolved_content
        
        # Fallback to manual eRT text
        fallback = state.get("ert_text", "") or ""
        if fallback in ("None", "none"):
            fallback = ""
        return fallback

    def apply_ert_rtplus_formatting(self, msg, content):
        """Apply RT+ formatting (prefix/suffix from first enabled default policy)."""
        try:
            policies = json.loads(msg.get("tagging_policies", "[]") or "[]")
            for p in policies:
                if p.get("enabled", True) and p.get("type") == "default":
                    s = p.get("settings", {})
                    prefix = s.get("prefix", "")
                    suffix = s.get("suffix", "")
                    if prefix or suffix:
                        return prefix + content + suffix
                    return content
        except Exception as e:
            print(f"eRT RT+ formatting error: {e}")
        return content

    def apply_rt_rtplus_formatting(self, msg, content):
        """Apply RT+ formatting (prefix/suffix from first enabled default policy) for regular RT."""
        try:
            policies = json.loads(msg.get("tagging_policies", "[]") or "[]")
            for p in policies:
                if p.get("enabled", True) and p.get("type") == "default":
                    s = p.get("settings", {})
                    prefix = s.get("prefix", "")
                    suffix = s.get("suffix", "")
                    if prefix or suffix:
                        return prefix + content + suffix
                    return content
        except Exception as e:
            print(f"RT RT+ formatting error: {e}")
        return content

    def freq_code(self, f):
        try: 
            freq_val = float(f)
            # Extended FM band support (87.5-108.0 MHz)
            if 87.5 <= freq_val <= 108.0:
                code = round((freq_val - 87.5) / 0.1)
                # Ensure code is within valid 8-bit range (0-204)
                return min(204, max(0, code))
            else:
                return 205  # "No AF exists" code
        except (ValueError, TypeError) as e:
            print(f"Warning: Invalid AF frequency '{f}': {e}")
            return 205

    def split(self, text, width=8, center=False):
        pad = lambda s: s.center(width) if center else s.ljust(width)
        # PS case (width<=8): preserve exact single short frame; otherwise word-wrap without splitting words
        if width <= 8:
            if text is None: return [pad("")]
            if len(text) <= width:
                return [pad(text)]
            # Word-wrap for readability, avoid splitting words; split overlong words only
            words, frames, curr = text.split(), [], ""
            for w in words:
                if len(w) > width:
                    if curr:
                        frames.append(pad(curr)); curr = ""
                    chunks = [w[i:i+width] for i in range(0, len(w), width)]
                    for c in chunks[:-1]:
                        frames.append(pad(c))
                    curr = chunks[-1]
                else:
                    test = (curr + " " + w).strip() if curr else w
                    if len(test) <= width:
                        curr = test
                    else:
                        # Optional heuristic: if current is very short (<4), keep it alone
                        frames.append(pad(curr)); curr = w
            if curr:
                frames.append(pad(curr))
            return frames

        # Wider fields (e.g., RT): keep existing word-wrap behavior
        words, frames, curr = text.split(), [], ""
        for w in words:
            if len(w) > width:
                if curr:
                    frames.append(pad(curr)); curr = ""
                chunks = [w[i:i+width] for i in range(0, len(w), width)]
                for c in chunks[:-1]:
                    frames.append(pad(c))
                curr = chunks[-1]
            else:
                test = (curr + " " + w).strip() if curr else w
                if len(test) <= width:
                    curr = test
                else:
                    frames.append(pad(curr)); curr = w
        if curr:
            frames.append(pad(curr))
        return frames

    def parse_smart(self, raw, width, center):
        seq = []
        if re.match(r"\s*\d+s:", raw):
            # Only treat timed syntax when the string starts with Ns: tokens.
            # Split on whitespace-delimited slashes so literal slashes remain intact.
            for p in re.split(r"\s*/\s*", raw):
                # Preserve trailing spaces in content; allow leading spaces before the number
                m = re.match(r"\s*(\d+)s:(.*)", p)
                if m:
                    content = m.group(2)
                    # Skip entries with blank/empty content (e.g., from empty file reads)
                    if not content.strip():
                        continue
                    for sf in self.split(content, width, center): seq.append((int(m.group(1)), sf))
                else:
                    if not p.strip():
                        continue
                    for sf in self.split(p.strip(), width, center): seq.append((2.5, sf))
        else:
            if width <= 8:
                # Preserve spaces for PS width
                if raw == "" or raw is None: return [(10, " "*width)]
                if len(raw) <= width: return [(10, self.split(raw, width, center)[0])]
                for sf in self.split(raw, width, center): seq.append((2.5, sf))
            else:
                # Trim for RT/others
                if not raw.strip(): return [(10, " "*width)]
                if len(raw.strip()) <= width: return [(10, self.split(raw, width, center)[0])]
                for sf in self.split(raw.strip(), width, center): seq.append((2.5, sf))
        # If all entries were skipped (all blank), return a fallback to avoid empty sequence
        if not seq:
            return [(10, " "*width)]
        return seq

    def parse_schedule_string(self, seq_str):
        out = []
        tokens = seq_str.upper().replace(',', ' ').split()
        for t in tokens:
            match = re.match(r"(\d+)([AB]?)", t)
            if match:
                grp = int(match.group(1))
                ver = 1 if match.group(2) == 'B' else 0
                out.append((grp, ver))
        return out if out else [(0,0)]

    def generate_auto_schedule(self):
        # Determine PS group version (0A or 0B)
        ps_ver = 1 if state.get("ps_group_version", "0A") == "0B" else 0

        # Build base sequence with core groups (0A/0B for PS, 2A for RT)
        # 57% Group 0, 43% 2A (rebalanced for better RT coverage)
        base_core = [(0,ps_ver), (0,ps_ver), (2,0), (0,ps_ver), (2,0), (0,ps_ver), (0,ps_ver), (0,ps_ver), (2,0), (0,ps_ver), (2,0), (0,ps_ver), (2,0), (0,ps_ver), (0,ps_ver), (2,0), (0,ps_ver), (2,0), (2,0), (2,0), (0,ps_ver), (0,ps_ver), (2,0)]

        # Collect all optional groups
        optional_groups = []

        if state["en_lps"]:
            optional_groups.append((15,0))
            optional_groups.append((15,0))  # +7% increase

        # EON at 15% - 4 consecutive groups for PS assembly (only if services configured)
        if state.get("en_eon"):
            try:
                eon_services_str = state.get("eon_services", "[]")
                eon_services = json.loads(eon_services_str) if isinstance(eon_services_str, str) else eon_services_str
                if eon_services:  # Only add if services exist
                    for _ in range(4):  # Send 4 Group 14A (~15% overall)
                        optional_groups.append((14,0))
            except:
                pass  # Skip EON if parsing fails

        if state["en_ptyn"]: optional_groups.append((10,0))  # +4% (single group)
        if state["en_id"]: optional_groups.append((1,0))

        # Transparent Data Channels
        if state.get("en_tdc_5a"): optional_groups.append((5,0))  # Group 5A
        if state.get("en_tdc_5b"): optional_groups.append((5,1))  # Group 5B

        # Group 3A is ODA announcement - add if any ODA is active
        needs_3a = False
        if state.get("en_dab"): needs_3a = True
        if state.get("en_rt_plus"): needs_3a = True
        if state.get("en_ert"): needs_3a = True  # Enhanced RadioText ODA
        if state.get("en_rds2"): needs_3a = True
        # Check if custom ODAs are enabled
        if not needs_3a:
            try:
                custom_oda_json = state.get("custom_oda_list", "[]")
                if custom_oda_json and custom_oda_json != "[]":
                    custom_odas = json.loads(custom_oda_json)
                    for oda in custom_odas:
                        if oda.get("enabled", True):
                            needs_3a = True
                            break
            except:
                pass
        if needs_3a:
            optional_groups.append((3,0))

        if state["en_rt_plus"]:
            # Send Group 11A (RT+) 3 times per cycle to ensure tags update quickly
            # RT+ tags need to be sent frequently so decoders can display current info
            optional_groups.append((11,0))
            optional_groups.append((11,0))
            optional_groups.append((11,0))

        # Add eRT application groups if enabled
        # Send 2x per cycle (like RT) to halve the time to complete a full message transmission
        if state.get("en_ert"):
            ert_group_type = state.get("ert_group_type", 11)
            optional_groups.append((ert_group_type, 0))  # eRT groups
            optional_groups.append((ert_group_type, 0))  # eRT groups x2

        if state.get("en_ert") and state.get("en_ert_rtplus"):
            ert_rtplus_group_type = state.get("ert_rtplus_group_type", 6)
            optional_groups.append((ert_rtplus_group_type, 0))  # eRT RT+ group (reduced from 4x to 1x for better balance)

        # Add enabled custom groups to auto schedule
        # Only add each unique group type once (or multiple times based on schedule_freq)
        # The custom group handler will cycle through all entries of that type
        try:
            custom_groups = self._get_custom_groups()

            # Collect unique group types with their max schedule_freq
            unique_groups = {}
            for custom in custom_groups:
                if custom.get("enabled", False):
                    group_type = custom.get("type", 0)
                    group_ver = custom.get("version", 0)
                    schedule_freq = custom.get("schedule_freq", 1)
                    key = (group_type, group_ver)
                    # Use the max schedule_freq if multiple entries exist
                    if key not in unique_groups or schedule_freq > unique_groups[key]:
                        unique_groups[key] = schedule_freq

            # Build list of all custom groups to add
            for (group_type, group_ver), freq in unique_groups.items():
                for _ in range(freq):
                    optional_groups.append((group_type, group_ver))
        except:
            pass  # Ignore errors loading custom groups

        # Now interleave core groups with optional groups
        # Strategy: Insert 0A groups at regular intervals to ensure decoders get PS frequently
        # For every 2-3 optional groups, inject a 0A group
        seq = []
        core_idx = 0

        if optional_groups:
            # Calculate how often to inject core groups
            # Aim for a 0A every 3-4 groups total
            inject_interval = 3

            for i, opt_group in enumerate(optional_groups):
                # Insert core groups before this optional group
                if i % inject_interval == 0:
                    # Insert 2 core groups (typically 0A, 0A or 0A, 2A)
                    for _ in range(2):
                        seq.append(base_core[core_idx % len(base_core)])
                        core_idx += 1

                # Add the optional group
                seq.append(opt_group)

            # Add remaining core groups at the end
            while core_idx < len(base_core):
                seq.append(base_core[core_idx])
                core_idx += 1
        else:
            # No optional groups, just use base core sequence
            seq = base_core

        self.schedule_gen_counter += 1
        return seq

    def next(self):
        global monitor_data
        now = datetime.now(timezone.utc)

        if state["en_ct"] and now.second == 0 and now.minute != self.ct_min_lock:
            self.ct_min_lock = now.minute
            mjd = (now.date() - date(1858, 11, 17)).days

            # Calculate automatic timezone offset (including DST)
            local_time = datetime.now().astimezone()
            auto_offset = local_time.utcoffset().total_seconds() / 3600

            # Add manual offset from UI for fine-tuning
            total_offset = auto_offset + state["tz_offset"]

            b4 = ((now.hour & 0x0F) << 12) | (now.minute << 6) | ((1 if total_offset<0 else 0) << 5) | int(abs(total_offset)*2)
            return RDSHelper.get_group_bits(4, 0, (mjd>>15)&3, ((mjd&0x7FFF)<<1)|((now.hour>>4)&1), b4)

        if state["scheduler_auto"]:
            # Recompute schedule only when relevant state changes or at the start of each cycle.
            sched_sig = (state["en_lps"], state.get("en_eon"), state["en_ptyn"], state["en_id"],
                         state.get("en_dab"), state["en_rt_plus"], state.get("en_tdc_5a"),
                         state.get("en_tdc_5b"), state.get("en_ert"), state.get("en_ert_rtplus"),
                         self._custom_groups_cache_str)
            cached = getattr(self, '_auto_schedule_cache', None)
            if (cached is None or self._auto_schedule_sig != sched_sig or
                    (self.schedule_ptr > 0 and self.schedule_ptr % len(cached) == 0)):
                self._auto_schedule_cache = self.generate_auto_schedule()
                self._auto_schedule_sig = sched_sig
            schedule = self._auto_schedule_cache
        else:
            schedule = self.parse_schedule_string(state["group_sequence"])

        if self.burst_counter > 0:
            g_type, g_ver = 0, 0
            self.burst_counter -= 1
        elif getattr(self, '_ert_rtplus_needs_clear', 0) > 0 and state.get("en_ert") and state.get("en_ert_rtplus"):
            # Inject an immediate 6A clear group ahead of the normal schedule.
            # This closes the window where the decoder holds stale tags from the previous
            # eRT message while the new 13A content is already being transmitted.
            self._ert_rtplus_needs_clear -= 1
            ertrtplus_type = state.get("ert_rtplus_group_type", 6)
            toggle = self.ert_rt_plus_toggle & 1
            b2_tail = (toggle << 4) | 0  # running_bit=0, no active tags
            return RDSHelper.get_group_bits(ertrtplus_type, 0, b2_tail, 0, 0)
        else:
            # Check if we should send DAB Group 12A (every 45 seconds)
            if state.get("en_dab") and (time.time() - self.dab_last_sent) >= 45.0:
                g_type, g_ver = 12, 0
                self.dab_last_sent = time.time()
            else:
                g_type, g_ver = schedule[self.schedule_ptr % len(schedule)]
                self.schedule_ptr += 1

        # Check for custom data groups FIRST (allows overriding built-in groups)
        try:
            custom_groups = self._get_custom_groups()

            # Debug: print loaded custom groups on first call (summary only)
            if not hasattr(self, '_custom_groups_debug_printed'):
                self._custom_groups_debug_printed = True
                enabled_count = sum(1 for cg in custom_groups if cg.get("enabled", False))
                if enabled_count > 0:
                    print(f"[Custom Groups] Loaded {enabled_count} enabled custom group(s) out of {len(custom_groups)} total")
        except Exception as e:
            custom_groups = []
            print(f"[Custom Groups] error: {e}")

        # Optional: Enable detailed debug logging with env var RDS_DEBUG_CUSTOM=1
        debug_custom = os.environ.get("RDS_DEBUG_CUSTOM", "0") == "1"

        if custom_groups:
            # Collect all enabled matching groups for this type+version
            matching_groups = []
            for custom in custom_groups:
                if not custom.get("enabled", False):
                    continue
                cg_type = custom.get("type", 0)
                cg_ver = custom.get("version", 0)
                if cg_type == g_type and cg_ver == g_ver:
                    matching_groups.append(custom)

            if matching_groups:
                # Cycle through matching groups using round-robin
                group_key = f"{g_type}{['A','B'][g_ver]}"
                if group_key not in self.custom_group_indices:
                    self.custom_group_indices[group_key] = 0

                # Get current index and cycle
                current_idx = self.custom_group_indices[group_key] % len(matching_groups)
                custom = matching_groups[current_idx]

                # Increment for next time
                self.custom_group_indices[group_key] = (current_idx + 1) % len(matching_groups)

                # Use selected custom group
                if debug_custom:
                    print(f"[Custom Groups] Sending Group {group_key} [{current_idx+1}/{len(matching_groups)}]: B2=0x{custom.get('b2_tail','00')} B3=0x{custom.get('b3','0000')} B4=0x{custom.get('b4','0000')}")
                try:
                    # Handle empty strings properly - treat empty/whitespace as "0"
                    b2_str = (custom.get("b2_tail", "0") or "0").strip() or "0"
                    b3_str = (custom.get("b3", "0") or "0").strip() or "0"
                    b4_str = (custom.get("b4", "0") or "0").strip() or "0"
                    
                    b2_tail = int(b2_str, 16) & 0x1F
                    b3_val = int(b3_str, 16) & 0xFFFF
                    b4_val = int(b4_str, 16) & 0xFFFF
                    
                    # Skip groups with all-zero data or placeholder data
                    # Common placeholders: 0x0000, 0xE0E0 (shown as "--" in some decoders)
                    if (b2_tail == 0 and b3_val == 0 and b4_val == 0) or \
                       (b3_val == 0xE0E0 and b4_val == 0) or \
                       (b3_val == 0 and b4_val == 0xE0E0) or \
                       (b3_val == 0xE0E0 and b4_val == 0xE0E0):
                        pass  # Skip this custom group, fall through to built-in handlers
                    else:
                        if custom.get("one_shot"):
                            # Remove by value equality (not identity) so the removal
                            # is correct even if the UECP thread updated the cache
                            # between when we read matching_groups and now.
                            # Only the first matching entry is removed.
                            all_groups = self._get_custom_groups()
                            new_groups = []
                            removed = False
                            for g in all_groups:
                                if not removed and g == custom:
                                    removed = True
                                else:
                                    new_groups.append(g)
                            state["custom_groups"] = json.dumps(new_groups)
                        return RDSHelper.get_group_bits(g_type, g_ver, b2_tail, b3_val, b4_val)
                except Exception as e:
                    print(f"[Custom Groups] Error creating group: {e}")
                    pass  # Invalid custom data, skip

        if g_type == 0:
            raw = self.get_text("ps_dynamic")
            sig = f"{raw}_{state['ps_centered']}"
            if sig != self.last_ps_content:
                self.last_ps_content, self.ps_ptr = sig, 0
                if state["scheduler_auto"]: self.burst_counter = 16 
                self.ps_sequence = self.parse_smart(raw, 8, state['ps_centered'])
                self.ps_seq_idx, self.ps_seq_start_time = 0, time.time()
            
            if not self.ps_sequence: self.ps_sequence = [(10, "RDS_PRO ")]
            dur, txt = self.ps_sequence[self.ps_seq_idx % len(self.ps_sequence)]
            
            txt = (txt or "").ljust(8)[:8]
            monitor_data["ps"] = txt

            # Convert PS text to RDS bytes (IEC 62106-4:2018)
            txt_bytes = text_to_rds_bytes(txt)

            if (time.time() - self.ps_seq_start_time) >= dur:
                self.ps_seq_idx += 1
                self.ps_seq_start_time, self.ps_ptr = time.time(), 0
                dur, txt = self.ps_sequence[self.ps_seq_idx % len(self.ps_sequence)]
                if state["scheduler_auto"] and self.ps_sequence[self.ps_seq_idx%len(self.ps_sequence)][1] != txt:
                    self.burst_counter = 12
                # Update bytes for new text
                txt_bytes = text_to_rds_bytes((txt or "").ljust(8)[:8])

            seg = self.ps_ptr % 4
            self.ps_ptr += 1
            tail = (get_effective_value("ta")<<4)|(get_effective_value("ms")<<3)|([state['di_dyn'],state['di_comp'],state['di_head'],state['di_stereo']][seg]<<2)|seg
            b3 = 0xE0E0

            # Group 0B: Block 3 contains PI code instead of AF
            if g_ver == 1:
                # For Group 0B, Block 3 is the PI code
                b3 = int(state.get("pi", "0000"), 16)
            elif state["en_af"] and g_ver == 0:
                 # Split by comma or space, accept both separators and validate frequencies
                 af_list_raw = state.get("af_list", "").strip()
                 if not af_list_raw:
                     # No AF list provided, don't send AF data
                     pass
                 else:
                     afs_raw = [x.strip() for x in re.split(r'[,\s]+', af_list_raw) if x.strip()]
                     # Validate and filter frequencies
                     afs = []
                     for freq in afs_raw:
                         try:
                             freq_val = float(freq)
                             if 87.5 <= freq_val <= 108.0:
                                 afs.append(freq)
                             else:
                                 print(f"Warning: AF frequency {freq} MHz is outside valid FM band (87.5-108.0)")
                         except (ValueError, TypeError):
                             print(f"Warning: Invalid AF frequency format: '{freq}'")
                     
                     af_method = state.get("af_method", "A")
                     af_pairs = []
                     af_pairs_json = "[]"

                     if afs and af_method == "A":
                         # Method A: List all frequencies with count code
                         try:
                             if self.af_ptr == 0:
                                 first_code = self.freq_code(afs[0])
                                 if first_code == 205:
                                     raise ValueError(f"Invalid first AF frequency: {afs[0]}")
                                 b3, self.af_ptr = (224+len(afs))<<8 | first_code, 1
                             else:
                                 # Check if we have more frequencies to send
                                 if self.af_ptr < len(afs):
                                     f1 = self.freq_code(afs[self.af_ptr])
                                     f2 = self.freq_code(afs[self.af_ptr+1]) if self.af_ptr+1 < len(afs) else 205
                                     if f1 == 205:
                                         print(f"Warning: Skipping invalid AF frequency: {afs[self.af_ptr]}")
                                         self.af_ptr += 1
                                         # Skip this iteration, try next frequency
                                         return self.next()
                                     b3, self.af_ptr = (f1<<8)|f2, (self.af_ptr+2) if self.af_ptr+2 < len(afs) else 0
                                 else:
                                     # All frequencies sent, loop back
                                     self.af_ptr = 0
                                     first_code = self.freq_code(afs[0])
                                     if first_code == 205:
                                         raise ValueError(f"Invalid first AF frequency on loop: {afs[0]}")
                                     b3 = (224+len(afs))<<8 | first_code
                         except (ValueError, IndexError) as e:
                             print(f"AF Method A encoding error: {e}")
                             # Reset AF state and skip AF transmission
                             self.af_ptr = 0

                     elif afs and af_method == "B":
                         # Method B: Frequency pairs with tuning frequency indicator
                         try:
                             af_pairs_json = state.get("af_pairs", "[]")
                             af_pairs = json.loads(af_pairs_json) if af_pairs_json else []
                         except Exception as e:
                             print(f"Warning: AF pairs JSON parsing error: {e}")
                             af_pairs = []

                         # Fallback: treat af_list as tuning=afs[0], alts=afs[1:]
                         if not af_pairs and afs:
                             af_pairs = [{
                                 'main': afs[0],
                                 'alts': ', '.join(afs[1:]) if len(afs) > 1 else '',
                                 'regional': False
                             }]

                         # Change-detection key includes af_list so fallback rebuilds when list changes
                         af_b_key = f"B|{af_pairs_json}|{af_list_raw}"

                         if af_pairs:
                             # Rebuild transmission list when anything relevant changes
                             if af_b_key != self._af_b_cache_key:
                                 self._af_b_cache_key = af_b_key
                                 self.af_b_transmissions = []
                                 self.af_b_ptr = 0

                                 # Group pairs by main frequency
                                 freq_groups = {}
                                 for pair in af_pairs:
                                     main_freq = pair.get('main', '').strip()
                                     alts_str = pair.get('alts', '')
                                     is_regional = pair.get('regional', False)
                                     if not main_freq:
                                         continue
                                     alt_freqs = [f.strip() for f in alts_str.split(',') if f.strip()]
                                     if not alt_freqs:
                                         continue
                                     if main_freq not in freq_groups:
                                         freq_groups[main_freq] = {'alts': [], 'regional': is_regional}
                                     freq_groups[main_freq]['alts'].extend(alt_freqs)
                                     if is_regional:
                                         freq_groups[main_freq]['regional'] = True

                                 for main_freq, group_data in freq_groups.items():
                                     alt_list = group_data['alts']
                                     is_regional = group_data['regional']
                                     total_freqs = 1 + (len(alt_list) * 2)
                                     tuning_indicator = 250 if is_regional else self.freq_code(main_freq)
                                     self.af_b_transmissions.append(((224 + total_freqs) << 8) | tuning_indicator)
                                     for alt_freq in alt_list:
                                         self.af_b_transmissions.append(
                                             (self.freq_code(main_freq) << 8) | self.freq_code(alt_freq)
                                         )

                             # Cycle through transmissions
                             if self.af_b_transmissions:
                                 b3 = self.af_b_transmissions[self.af_b_ptr % len(self.af_b_transmissions)]
                                 self.af_b_ptr = (self.af_b_ptr + 1) % len(self.af_b_transmissions)
                             else:
                                 b3 = 0xE0E0
                         else:
                             b3 = 0xE0E0
                             self.af_b_ptr = 0
            
            if g_ver == 1: b3 = int(state["pi"], 16)
            return RDSHelper.get_group_bits(0, g_ver, tail, b3, (txt_bytes[seg*2]<<8)|txt_bytes[seg*2+1])

        elif g_type == 2:
            limit = 32 if state["rt_mode"] == "2B" else 64
            current_msg = None

            # Quick check: if RT text is "None" or empty AND no messages configured, skip Group 2A entirely
            rt_text_check = get_effective_value("rt_text") or ""
            rt_messages = self.get_rt_messages()
            if rt_text_check.strip() in ["", "None"] and not rt_messages:
                # No valid RT - advance schedule and get next group type
                if state["scheduler_auto"]:
                    schedule = getattr(self, '_auto_schedule_cache', [(0,0)])
                else:
                    schedule = self.parse_schedule_string(state["group_sequence"])
                g_type, g_ver = schedule[self.schedule_ptr % len(schedule)]
                self.schedule_ptr += 1
                return self.next()  # Recursively get next group

            # Check for new unified message system first
            if rt_messages:
                # New unified message system
                current_msg, buf, raw = self.get_current_rt_message()
                if not raw:
                    raw = " " * limit  # Fallback to blank

                # Apply RT+ formatting (prefix/suffix) if enabled for this message
                if current_msg and current_msg.get("rt_plus_enabled", False):
                    raw = self.apply_rt_rtplus_formatting(current_msg, raw)

                # Truncate to limit
                raw = raw[:limit]

            elif state.get("rt_auto_ab"):
                # AUTO buffer mode: single source, flip A/B only when content changes.
                # Starts on buffer A; no time-based cycling.
                raw_input = self.get_text("rt_text")
                raw = raw_input.strip()[:limit]
                if self.last_rt_text_content == "":
                    self.last_rt_text_content = raw  # seed without flipping (start on A)
                elif raw != self.last_rt_text_content:
                    self.last_rt_text_content = raw
                    self.rt_ab_flag = 1 - self.rt_ab_flag
                    self.rt_ptr = 0
                buf = self.rt_ab_flag

            elif state["rt_manual_buffers"]:
                # Legacy manual mode: use traditional cycling
                buf = int((time.time()-self.start_time)/state["rt_cycle_time"])%2 if state["rt_cycle"] else state["rt_active_buffer"]
                raw = self.get_text("rt_a" if buf==0 else "rt_b")
            else:
                # Legacy auto mode: single buffer with sequence support
                raw_input = self.get_text("rt_text")

                # Check if we need to rebuild the sequence
                if not self.rt_sequence or raw_input != self.last_rt_text_content:
                    if "/" in raw_input:
                        self.rt_sequence = self.parse_smart(raw_input, limit, False)
                    else:
                        m = re.match(r"\s*(\d+)s:(.*)", raw_input.strip())
                        if m:
                            duration = int(m.group(1))
                            text = m.group(2).strip()[:limit]
                        else:
                            duration = 10
                            text = raw_input.strip()[:limit]
                        self.rt_sequence = [(duration, text)]

                    self.rt_seq_idx = 0
                    self.rt_seq_start_time = time.time()
                    self.last_rt_text_content = raw_input
                    self.rt_ab_flag = 1 - self.rt_ab_flag
                    self.rt_ab_cycles = 0
                    self.rt_ptr = 0

                dur, txt = self.rt_sequence[self.rt_seq_idx % len(self.rt_sequence)]

                if len(self.rt_sequence) > 1 and time.time() - self.rt_seq_start_time >= dur:
                    self.rt_seq_idx += 1
                    self.rt_seq_start_time = time.time()
                    dur, txt = self.rt_sequence[self.rt_seq_idx % len(self.rt_sequence)]
                    if not state["rt_cycle_ab"]:
                        self.rt_ab_flag = 1 - self.rt_ab_flag

                buf = self.rt_ab_flag
                raw = txt.strip()

            # If A/B buffer changed, restart RT from the beginning
            if buf != self.last_rt_buf:
                self.rt_ptr = 0
                self.last_rt_buf = buf

            sig = f"{raw}_{state['rt_centered']}_{state['rt_cr']}_{state.get('rt_disable_0d', False)}"

            # Calculate RT+ tags
            if current_msg:
                # New message system: use per-message RT+ config
                # Include all RT+ settings that could affect tag calculation in the signature
                rt_plus_tags_config = current_msg.get('rt_plus_tags', {})
                rt_plus_sig = f"{raw}_{current_msg.get('id')}_{current_msg.get('rt_plus_enabled')}_{current_msg.get('split_delimiter')}_{current_msg.get('rt_plus_mode','')}_{current_msg.get('rt_plus_regex_rules','')}_{rt_plus_tags_config.get('tag1_type')}_{rt_plus_tags_config.get('tag2_type')}_{current_msg.get('json_field1')}_{current_msg.get('json_field2')}_{current_msg.get('tagging_policies','')}"
                if raw != self.last_rt_clean or rt_plus_sig != getattr(self, 'last_rtplus_sig', ''):
                    self.last_rt_clean = raw
                    self.last_rtplus_sig = rt_plus_sig
                    self.rt_plus_toggle = 1 - self.rt_plus_toggle
                    self.rt_plus_tags = self.get_rt_plus_tags_for_message(current_msg, raw, limit)

                    tag_str = []
                    # Use same terminator logic for RT+ tag display
                    term_char = ' ' if (state["rt_cr"] and state.get("rt_disable_0d", False)) else '\r' if state["rt_cr"] else ''
                    display_clean = (raw + term_char) if term_char else (raw.center(limit) if state["rt_centered"] else raw.ljust(limit))
                    for t in self.rt_plus_tags:
                        t_name = RTPLUS_CONTENT_TYPES.get(t[0], ("Unknown", ""))[0]
                        content = display_clean[t[1]:t[1]+t[2]]
                        tag_str.append(f"{t_name}: {content}")
                    monitor_data["rt_plus_info"] = " | ".join(tag_str) if tag_str else "(no tags)"
            else:
                # Legacy RT+ handling
                builder_key = "rt_plus_builder_a" if buf == 0 else "rt_plus_builder_b"
                builder_state = state.get(builder_key, "")
                rt_plus_sig = f"{raw}_{state.get('rt_plus_mode')}_{builder_state}_{state.get('rt_plus_format_a')}_{state.get('rt_plus_format_b')}_{state.get('rt_plus_regex_rules_a')}_{state.get('rt_plus_regex_rules_b')}"

                if raw != self.last_rt_clean or rt_plus_sig != getattr(self, 'last_rtplus_sig', ''):
                    self.last_rt_clean = raw
                    self.last_rtplus_sig = rt_plus_sig
                    self.rt_plus_toggle = 1 - self.rt_plus_toggle

                    if state.get('rt_plus_mode') == 'builder' and builder_state:
                        self.rt_plus_tags = RTPlusParser.parse(raw, None, centered=state['rt_centered'], limit=limit, builder_state=builder_state)
                    elif state.get('rt_plus_mode') == 'regex':
                        rules_key = "rt_plus_regex_rules_a" if buf == 0 else "rt_plus_regex_rules_b"
                        _offset = (limit - len(raw)) // 2 if state['rt_centered'] and len(raw) < limit else 0
                        self.rt_plus_tags = RTPlusParser.parse_regex_rules(raw, state.get(rules_key, "[]"), _offset, limit)
                    else:
                        fmt = state["rt_plus_format_a"] if buf == 0 else state["rt_plus_format_b"]
                        self.rt_plus_tags = RTPlusParser.parse(raw, fmt, centered=state['rt_centered'], limit=limit)

                    tag_str = []
                    # Use same terminator logic for RT+ tag display
                    term_char = ' ' if (state["rt_cr"] and state.get("rt_disable_0d", False)) else '\r' if state["rt_cr"] else ''
                    display_clean = (raw + term_char) if term_char else (raw.center(limit) if state["rt_centered"] else raw.ljust(limit))
                    for t in self.rt_plus_tags:
                        t_name = RTPLUS_CONTENT_TYPES.get(t[0], ("Unknown", ""))[0]
                        content = display_clean[t[1]:t[1]+t[2]]
                        tag_str.append(f"{t_name}: {content}")
                    monitor_data["rt_plus_info"] = " | ".join(tag_str) if tag_str else "(no tags)"

            if sig != self.last_rt_content: self.rt_ptr, self.last_rt_content = 0, sig
            # Determine terminator: space if disable_0d is enabled, otherwise CR
            terminator = ' ' if (state["rt_cr"] and state.get("rt_disable_0d", False)) else '\r' if state["rt_cr"] else ''
            if terminator:
                clean = raw + terminator
            else:
                clean = raw.center(limit) if state["rt_centered"] else raw.ljust(limit)

            monitor_data["rt"] = clean

            v = g_ver
            bpg = 2 if v==1 else 4
            if self.rt_ptr * bpg >= len(clean) or (clean.find('\r') != -1 and self.rt_ptr*bpg > clean.find('\r')):
                # Completed one full RT transmission cycle
                if current_msg:
                    # New message system: cycle-based advancement
                    self.rt_msg_cycle_count += 1
                    cycle_limit = current_msg.get("cycles", 2)
                    if cycle_limit < 1:
                        cycle_limit = 1

                    buffer_setting = current_msg.get("buffer", "AB")
                    if buffer_setting == "AUTO":
                        # AUTO: buffer only flips when content changes (handled in
                        # get_current_rt_message). Never cycle-advance — keep
                        # transmitting indefinitely until the source changes.
                        pass
                    elif buffer_setting == "AB":
                        # For AB messages: do N cycles on buffer A, then N cycles on buffer B
                        # Check if we've completed N cycles on current buffer
                        if self.rt_msg_cycle_count == cycle_limit:
                            # Completed N cycles on buffer A, switch to buffer B
                            self.rt_ab_flag = 1 - self.rt_ab_flag
                        elif self.rt_msg_cycle_count >= cycle_limit * 2:
                            # Completed N cycles on both A and B, advance to next message
                            # Toggle buffer so next message starts on opposite buffer
                            self.advance_to_next_rt_message(toggle_buffer=True)
                    else:
                        # For A or B only messages: just count cycles
                        if self.rt_msg_cycle_count >= cycle_limit:
                            # Toggle buffer so next message doesn't overwrite
                            self.advance_to_next_rt_message(toggle_buffer=True)
                elif not current_msg and state.get("rt_cycle_ab"):
                    # Legacy cycle_ab handling
                    self.rt_ab_cycles += 1
                    try:
                        cycle_target = int(state.get("rt_ab_cycle_count", 2))
                    except:
                        cycle_target = 2
                    if cycle_target < 1: cycle_target = 1
                    if self.rt_ab_cycles >= cycle_target:
                        self.rt_ab_flag = 1 - self.rt_ab_flag
                        self.rt_ab_cycles = 0
                self.rt_ptr = 0
            pad = clean.ljust(64)

            # Encode to RDS character encoding (IEC 62106-4:2018)
            pad_bytes = text_to_rds_bytes(pad)

            # Ensure it's 64 bytes long
            if len(pad_bytes) < 64:
                pad_bytes = pad_bytes + b' ' * (64 - len(pad_bytes))
            else:
                pad_bytes = pad_bytes[:64]

            a = self.rt_ptr % 16
            self.rt_ptr += 1
            # Group 2B Fix: Block 3 is PI, Block 4 is 2 chars of RT
            # Use byte values directly instead of ord() to properly handle extended characters
            b3_val = (pad_bytes[a*4]<<8)|pad_bytes[a*4+1] if v==0 else int(state["pi"], 16)
            b4_val = (pad_bytes[a*4+2]<<8)|pad_bytes[a*4+3] if v==0 else (pad_bytes[a*2]<<8)|pad_bytes[a*2+1]
            return RDSHelper.get_group_bits(2, v, (buf<<4)|a, b3_val, b4_val)

        elif g_type == 3:
             # Group 3A: ODA announcement for DAB, RT+, and custom ODAs
             # Build list of all active ODAs
             active_odas = []

             # Add DAB if enabled
             if state.get("en_dab"):
                 active_odas.append({"type": "dab", "group_type": 0x18, "aid": 0x0093, "msg": 0x0000})

             # Add RT+ if enabled
             if state.get("en_rt_plus"):
                 # RT+ uses 11A, so AGTC = (11*2) + 0 = 22
                 active_odas.append({"type": "rtplus", "group_type": 22, "aid": 0x4BD7, "msg": 0x0000})
             
             # Add eRT if enabled
             if state.get("en_ert"):
                 ert_group_type = state.get("ert_group_type", 13)  # Default to 13A instead of 11A
                 # eRT AGTC = (group_number*2) + 0 for version A
                 ert_agtc = (ert_group_type * 2) + 0
                 active_odas.append({"type": "ert", "group_type": ert_agtc, "aid": 0x6552, "msg": self.get_ert_control_bits()})
             
             # Add eRT RT+ if enabled (separate ODA on its own dedicated group)
             if state.get("en_ert") and state.get("en_ert_rtplus"):
                 ert_rtplus_group_type = state.get("ert_rtplus_group_type", 6)
                 # eRT RT+ AGTC = (group_number*2) + 0 for version A
                 ert_rtplus_agtc = (ert_rtplus_group_type * 2) + 0
                 active_odas.append({"type": "ert_rtplus", "group_type": ert_rtplus_agtc, "aid": 0x4BD8, "msg": 0x0000})

             # NOTE: RDS2 ODA is NOT included here - it's only announced in the RDS2 stream, not in regular RDS

             # Add custom ODAs
             try:
                 custom_oda_json = state.get("custom_oda_list", "[]")
                 if custom_oda_json and custom_oda_json != "[]":
                     custom_odas = json.loads(custom_oda_json)
                     for oda in custom_odas:
                         if oda.get("enabled", True):
                             aid_val = oda.get("aid", "0000")
                             msg_val = oda.get("msg", "0000")
                             active_odas.append({
                                 "type": "custom",
                                 "group_type": int(oda.get("group_type", 0)),
                                 "aid": int(aid_val, 16) if isinstance(aid_val, str) else int(aid_val),
                                 "msg": int(msg_val, 16) if isinstance(msg_val, str) else int(msg_val)
                             })
             except Exception as e:
                 print(f"Error parsing custom ODAs: {e}")
             
             # If we have active ODAs, cycle through them
             if active_odas:
                 # Use modulo to cycle through list
                 current_oda = active_odas[self.group_3a_toggle % len(active_odas)]
                 self.group_3a_toggle = (self.group_3a_toggle + 1) % len(active_odas)
                 
                 # Emit ODA announcement
                 # Application Group Type Code is now pre-encoded in the ODA list
                 app_group_code = current_oda["group_type"] & 0x1F  # Ensure 5-bit value
                 return RDSHelper.get_group_bits(3, 0, app_group_code, 
                                                  current_oda["msg"], current_oda["aid"])
        
        # Enhanced RadioText (eRT) Application Groups
        elif g_type == state.get("ert_group_type", 13) and state.get("en_ert"):
            return self.generate_ert_group(g_ver)

        # eRT RT+ (ODA AID: 0x4BD8) — same wire format as Group 11A RT+ but for eRT content
        elif g_type == state.get("ert_rtplus_group_type", 6) and state.get("en_ert") and state.get("en_ert_rtplus"):
            t1_typ, t1_start, t1_len = 0, 0, 0
            t2_typ, t2_start, t2_len = 0, 0, 0

            tags_to_send = list(self.ert_rt_plus_tags[:2])

            # SMART SORT: Tag 2 slot has 5-bit length field (max 32). Tag 1 has 6-bit (max 64).
            # If we have 2 tags, put the longer-content one in the Tag 1 slot.
            if len(tags_to_send) == 2:
                if tags_to_send[1][2] > 31 and tags_to_send[0][2] <= 31:
                    tags_to_send.reverse()

            if len(tags_to_send) > 0:
                t1_typ, t1_start, t1_len = tags_to_send[0]
                if t1_len > 0: t1_len -= 1  # Spec: length marker is len-1
            if len(tags_to_send) > 1:
                t2_typ, t2_start, t2_len = tags_to_send[1]
                if t2_len > 0: t2_len -= 1

            toggle = self.ert_rt_plus_toggle & 1
            # Suppress tags (running=0) until the decoder has a full copy of the current eRT text
            running_bit = 1 if (tags_to_send and not self._ert_content_new) else 0
            b2_tail = (toggle << 4) | (running_bit << 3) | ((t1_typ >> 3) & 0x07)
            b3_val  = ((t1_typ & 0x07) << 13) | ((t1_start & 0x3F) << 7) | ((t1_len & 0x3F) << 1) | ((t2_typ >> 5) & 0x01)
            b4_val  = ((t2_typ & 0x1F) << 11) | ((t2_start & 0x3F) << 5) | (t2_len & 0x1F)

            return RDSHelper.get_group_bits(state.get("ert_rtplus_group_type", 6), 0, b2_tail, b3_val, b4_val)

        elif g_type == 5:
            # Group 5A/5B: Transparent Data Channels (TDC)
            # Type 5A: Full transparent data (4 segments)
            # Type 5B: PI code in block 3 + transparent data

            # Initialize TDC state tracking if needed
            if not hasattr(self, 'tdc_5a_ptr'):
                self.tdc_5a_ptr = 0
                self.tdc_5b_ptr = 0
                self.tdc_last_text_5a = ""
                self.tdc_last_text_5b = ""

            # Get the text to send based on mode
            if g_ver == 0:  # 5A
                # Check if dynamic control is providing text
                if "tdc_5a_text" in dynamic_overrides and dynamic_overrides["tdc_5a_text"]:
                    # Dynamic control active - use the override text
                    text = dynamic_overrides["tdc_5a_text"]
                    # For custom text, only reset pointer if text actually changed
                    if text != self.tdc_last_text_5a:
                        self.tdc_last_text_5a = text
                        self.tdc_5a_ptr = 0
                else:
                    # No dynamic override - use local mode setting (fallback to PC status)
                    mode = state.get("tdc_5a_mode", "custom")
                    if mode == "pc_status":
                        text = PCStatusMonitor.format_pc_status()
                        # For PC status, reset pointer if text changed (e.g., uptime length changed)
                        if text != self.tdc_last_text_5a:
                            self.tdc_5a_ptr = 0
                        self.tdc_last_text_5a = text
                    else:
                        text = state.get("tdc_5a_text", "")
                        # Support external sources (file/URL patterns) like RT/PS
                        if "\\" in text:
                            text = parse_text_source(text, self.tdc_last_text_5a) or text
                        # For custom text, only reset pointer if text actually changed
                        if text != self.tdc_last_text_5a:
                            self.tdc_last_text_5a = text
                            self.tdc_5a_ptr = 0

                # Support up to 128 characters with CR terminator
                text = text[:128]  # Truncate to 128 chars max

                # Convert to RDS bytes and add CR (0x0D) terminator
                text_bytes = text_to_rds_bytes(text)
                actual_length = len(text_bytes)

                # Add CR terminator if there's room
                if actual_length < 128:
                    text_bytes = text_bytes + bytes([0x0D])  # Carriage Return
                    actual_length += 1

                # Calculate segments needed (round up to nearest segment boundary)
                # Each segment is 4 bytes, so divide by 4 and round up
                num_segments = (actual_length + 3) // 4  # Round up division

                # Pad to segment boundary
                text_bytes = text_bytes.ljust(num_segments * 4, b'\x20')

                # Cycle through only the segments we need
                segment = self.tdc_5a_ptr % num_segments
                self.tdc_5a_ptr += 1

                # Block 2 tail: 5-bit channel number (0-31)
                channel = state.get("tdc_5a_channel", 0)
                b2_tail = channel & 0x1F

                # Blocks 3 and 4: 4 bytes of data (2 per block)
                offset = segment * 4
                b3_val = (text_bytes[offset] << 8) | text_bytes[offset + 1]
                b4_val = (text_bytes[offset + 2] << 8) | text_bytes[offset + 3]

                return RDSHelper.get_group_bits(5, 0, b2_tail, b3_val, b4_val)

            else:  # 5B
                # Check if dynamic control is providing text
                if "tdc_5b_text" in dynamic_overrides and dynamic_overrides["tdc_5b_text"]:
                    # Dynamic control active - use the override text
                    text = dynamic_overrides["tdc_5b_text"]
                    # For custom text, only reset pointer if text actually changed
                    if text != self.tdc_last_text_5b:
                        self.tdc_last_text_5b = text
                        self.tdc_5b_ptr = 0
                else:
                    # No dynamic override - use local mode setting (fallback to PC status)
                    mode = state.get("tdc_5b_mode", "custom")
                    if mode == "pc_status":
                        text = PCStatusMonitor.format_pc_status()
                        # For PC status, reset pointer if text changed (e.g., uptime length changed)
                        if text != self.tdc_last_text_5b:
                            self.tdc_5b_ptr = 0
                        self.tdc_last_text_5b = text
                    else:
                        text = state.get("tdc_5b_text", "")
                        # Support external sources (file/URL patterns) like RT/PS
                        if "\\" in text:
                            text = parse_text_source(text, self.tdc_last_text_5b) or text
                        # For custom text, only reset pointer if text actually changed
                        if text != self.tdc_last_text_5b:
                            self.tdc_last_text_5b = text
                            self.tdc_5b_ptr = 0

                # Type 5B has PI in block 3, so only 2 bytes in block 4
                # Support up to 128 characters with CR terminator
                text = text[:128]  # Truncate to 128 chars max

                # Convert to RDS bytes and add CR (0x0D) terminator
                text_bytes = text_to_rds_bytes(text)
                actual_length = len(text_bytes)

                # Add CR terminator if there's room
                if actual_length < 128:
                    text_bytes = text_bytes + bytes([0x0D])  # Carriage Return
                    actual_length += 1

                # Calculate segments needed (round up to nearest segment boundary)  
                # Each segment is 4 bytes (like 5A), so divide by 4 and round up
                num_segments = (actual_length + 3) // 4  # Round up division

                # Pad to segment boundary
                text_bytes = text_bytes.ljust(num_segments * 4, b'\x20')

                # Cycle through only the segments we need
                segment = self.tdc_5b_ptr % num_segments
                self.tdc_5b_ptr += 1

                # Block 2 tail: 5-bit channel number (0-31)
                channel = state.get("tdc_5b_channel", 0)
                b2_tail = channel & 0x1F

                # Blocks 3 and 4: 4 bytes of data (2 per block) - same as 5A
                offset = segment * 4
                b3_val = (text_bytes[offset] << 8) | text_bytes[offset + 1]
                b4_val = (text_bytes[offset + 2] << 8) | text_bytes[offset + 3]

                return RDSHelper.get_group_bits(5, 1, b2_tail, b3_val, b4_val)

        elif g_type == 11 and (not state["scheduler_auto"] or state["en_rt_plus"]):
             # --- CORRECTED RT+ PACKING (37 Bits split across blocks) ---
             # Supports 0, 1, or 2 tags. Running bit indicates whether tags are active.

             t1_typ, t1_start, t1_len = 0, 0, 0
             t2_typ, t2_start, t2_len = 0, 0, 0

             tags_to_send = self.rt_plus_tags[:2]

             # SMART SORT: Tag 2 has 5-bit length (max 32). Tag 1 has 6-bit (max 64).
             # If we have 2 tags, put the longer one in Tag 1 slot.
             if len(tags_to_send) == 2:
                 if tags_to_send[1][2] > 31 and tags_to_send[0][2] <= 31:
                     tags_to_send.reverse()

             if len(tags_to_send) > 0:
                 t1_typ, t1_start, t1_len = tags_to_send[0]
                 # Spec: Length marker is len-1
                 if t1_len > 0: t1_len -= 1

             if len(tags_to_send) > 1:
                 t2_typ, t2_start, t2_len = tags_to_send[1]
                 if t2_len > 0: t2_len -= 1

             # Block 2 Tail (5 bits): Toggle(1) | Running(1) | T1_Type_Hi3(3)
             # Note: Running bit is bit 3 (value 8). Toggle is bit 4 (value 16).
             # Running bit should only be set when tags are actually present
             running_bit = 1 if tags_to_send else 0
             b2_tail = ((self.rt_plus_toggle & 1) << 4) | (running_bit << 3) | ((t1_typ >> 3) & 0x07)
             
             # Block 3 (16 bits): T1_Type_Lo3(3) | T1_Start(6) | T1_Len(6) | T2_Type_Hi1(1)
             b3_val = ((t1_typ & 0x07) << 13) | ((t1_start & 0x3F) << 7) | ((t1_len & 0x3F) << 1) | ((t2_typ >> 5) & 0x01)
             
             # Block 4 (16 bits): T2_Type_Lo5(5) | T2_Start(6) | T2_Len(5)
             b4_val = ((t2_typ & 0x1F) << 11) | ((t2_start & 0x3F) << 5) | (t2_len & 0x1F)
             
             return RDSHelper.get_group_bits(11, 0, b2_tail, b3_val, b4_val)

        elif g_type == 14 and (not state["scheduler_auto"] or state.get("en_eon")):
            # Group 14A: Enhanced Other Networks (EON) - EN 50067 section 3.2.1.8
            eon_services = []
            eon_services_str = state.get("eon_services", "[]")

            try:
                if isinstance(eon_services_str, str):
                    eon_services = json.loads(eon_services_str)
                elif isinstance(eon_services_str, list):
                    eon_services = eon_services_str
                else:
                    eon_services = []
            except Exception as e:
                eon_services = []

            if not eon_services:
                # No services configured - skip Group 14A entirely
                # Advance schedule pointer and get next group
                if state["scheduler_auto"]:
                    schedule = getattr(self, '_auto_schedule_cache', [(0,0)])
                else:
                    schedule = self.parse_schedule_string(state["group_sequence"])
                g_type, g_ver = schedule[self.schedule_ptr % len(schedule)]
                self.schedule_ptr += 1
                # Fall through to next group (will be handled by subsequent checks)
                # Avoid recursion - just return a basic 0A PS group as fallback
                return self.next()

            # Initialize EON state tracking
            if not hasattr(self, 'eon_service_idx'):
                self.eon_service_idx = 0
                self.eon_variant = 0  # 0=PS, 4=AF list, 5-8=mapped, 9=LF/MF, 10=other band, 13=PTY+TA
                self.eon_ps_seg = 0   # PS segment (0-3)
                self.eon_af_idx = -1  # AF index (-1=count code, 0+=frequencies)
                self.eon_mapped_idx = 0  # Index for mapped frequency pairs

            service = eon_services[self.eon_service_idx % len(eon_services)]

            # Get PI(ON) for block 3 (14B) or block 4 (14A)
            try:
                pi_on = int(service.get('pi_on', 'C000'), 16) & 0xFFFF
            except:
                pi_on = 0xC000

            # Get TP(ON) for block 2
            tp_on = service.get('tp', 0) & 1

            # Variant 0: PS(ON) - 2 characters per transmission, 4 segments total
            if self.eon_variant == 0:
                ps_text = service.get('ps', 'OTHER   ')[:8].ljust(8)

                # Encode to RDS character encoding (IEC 62106-4:2018)
                ps_bytes = text_to_rds_bytes(ps_text)
                if len(ps_bytes) < 8:
                    ps_bytes = ps_bytes + b' ' * (8 - len(ps_bytes))
                else:
                    ps_bytes = ps_bytes[:8]

                # Block 2 tail for variant 0: TP(ON) in bit 4, variant bits 000SS where SS=segment
                b2_tail = (tp_on << 4) | (self.eon_ps_seg & 0x03)

                # Send 2 characters for current segment
                b3_val = (ps_bytes[self.eon_ps_seg*2] << 8) | ps_bytes[self.eon_ps_seg*2 + 1]

                # Advance to next PS segment
                self.eon_ps_seg += 1
                if self.eon_ps_seg >= 4:
                    self.eon_ps_seg = 0
                    # After PS, check for mapped frequencies first, then AF list
                    mapped_freqs = service.get('mapped_freqs', [])
                    lf_mf_mapped = service.get('lf_mf_mapped', [])
                    af_list = service.get('af_list', '')
                    afs = [f.strip() for f in af_list.split(',') if f.strip()]
                    
                    # Priority: mapped freqs (variants 5-8) > LF/MF (9) > other band (10) > AF list (4)
                    if mapped_freqs:
                        self.eon_variant = 5  # Start with variant 5 (first mapped pair)
                        self.eon_mapped_idx = 0
                    elif lf_mf_mapped:
                        self.eon_variant = 9  # LF/MF mapped
                        self.eon_mapped_idx = 0
                    elif afs:
                        self.eon_variant = 4  # AF list (Method A)
                        self.eon_af_idx = -1
                    else:
                        self.eon_variant = 13  # Skip to PTY+TA if no AF data

            # Variants 5-8: Mapped Frequency Pairs (same band)
            # Variant 5 = 1st pair, 6 = 2nd pair, 7 = 3rd pair, 8 = 4th pair
            elif self.eon_variant >= 5 and self.eon_variant <= 8:
                mapped_freqs = service.get('mapped_freqs', [])
                
                if self.eon_mapped_idx < len(mapped_freqs):
                    pair = mapped_freqs[self.eon_mapped_idx]
                    tuned_freq = pair.get('tuned', '')
                    other_freq = pair.get('other', '')
                    
                    try:
                        # Check if frequencies are in the same band (both 87.6-107.9 or both 64.1-88.0)
                        tuned_f = float(tuned_freq)
                        other_f = float(other_freq)
                        
                        # Determine which band each frequency is in
                        tuned_band2 = 87.6 <= tuned_f <= 107.9
                        other_band2 = 87.6 <= other_f <= 107.9
                        tuned_band1 = 64.1 <= tuned_f <= 88.0
                        other_band1 = 64.1 <= other_f <= 88.0
                        
                        if (tuned_band2 and other_band2) or (tuned_band1 and other_band1):
                            # Same band - use variants 5-8
                            b2_tail = (tp_on << 4) | self.eon_variant
                            
                            if tuned_band2:
                                # Both in Band II (87.6-107.9 MHz) - standard coding
                                tuned_code = self.freq_code(tuned_freq)
                                other_code = self.freq_code(other_freq)
                            else:
                                # Both in Band I (64.1-88.0 MHz) - extended coding minus 256
                                tuned_code = (round((tuned_f - 64.1) / 0.1) + 257 - 256) & 0xFF
                                other_code = (round((other_f - 64.1) / 0.1) + 257 - 256) & 0xFF
                            
                            b3_val = (tuned_code << 8) | other_code
                        elif (tuned_band2 and other_band1) or (tuned_band1 and other_band2):
                            # Different bands - use variant 10
                            b2_tail = (tp_on << 4) | 0x0A  # Variant 10
                            
                            # For variant 10, encode using appropriate method for each band
                            if tuned_band2:
                                tuned_code = self.freq_code(tuned_freq)
                            else:
                                tuned_code = (round((tuned_f - 64.1) / 0.1) + 257 - 256) & 0xFF
                            
                            if other_band2:
                                other_code = self.freq_code(other_freq)
                            else:
                                other_code = (round((other_f - 64.1) / 0.1) + 257 - 256) & 0xFF
                            
                            b3_val = (tuned_code << 8) | other_code
                        else:
                            # Invalid frequencies
                            b2_tail = (tp_on << 4) | self.eon_variant
                            b3_val = (205 << 8) | 205
                    except:
                        # Parsing error - send filler
                        b2_tail = (tp_on << 4) | self.eon_variant
                        b3_val = (205 << 8) | 205
                    
                    # Advance to next mapped pair
                    self.eon_mapped_idx += 1
                    if self.eon_mapped_idx < len(mapped_freqs) and self.eon_variant < 8:
                        self.eon_variant += 1  # Next variant (6, 7, or 8)
                    else:
                        # Done with mapped frequencies
                        # Check for LF/MF mapped or AF list
                        lf_mf_mapped = service.get('lf_mf_mapped', [])
                        af_list = service.get('af_list', '')
                        afs = [f.strip() for f in af_list.split(',') if f.strip()]
                        
                        if lf_mf_mapped:
                            self.eon_variant = 9
                            self.eon_mapped_idx = 0
                        elif afs:
                            self.eon_variant = 4
                            self.eon_af_idx = -1
                        else:
                            self.eon_variant = 13
                else:
                    # No more mapped pairs - skip to next variant
                    lf_mf_mapped = service.get('lf_mf_mapped', [])
                    af_list = service.get('af_list', '')
                    afs = [f.strip() for f in af_list.split(',') if f.strip()]
                    
                    if lf_mf_mapped:
                        self.eon_variant = 9
                        self.eon_mapped_idx = 0
                        b2_tail = (tp_on << 4) | 0x09
                        b3_val = 0xE0E0  # This will be overridden in next call
                    elif afs:
                        self.eon_variant = 4
                        self.eon_af_idx = -1
                        b2_tail = (tp_on << 4) | 0x04
                        b3_val = 0xE0E0
                    else:
                        self.eon_variant = 13
                        b2_tail = (tp_on << 4) | 0x0D
                        b3_val = 0xE0E0

            # Variant 9: LF/MF Mapped Frequencies
            elif self.eon_variant == 9:
                lf_mf_mapped = service.get('lf_mf_mapped', [])
                
                if self.eon_mapped_idx < len(lf_mf_mapped):
                    pair = lf_mf_mapped[self.eon_mapped_idx]
                    vhf_freq = pair.get('vhf', '')
                    lf_mf_freq = pair.get('lf_mf', 0)
                    
                    try:
                        # VHF frequency code (standard AF coding)
                        vhf_code = self.freq_code(vhf_freq)
                        
                        # LF/MF frequency in kHz (16-bit value in block 4 for variant 9)
                        # But for block 3, we use special encoding
                        # According to spec: variant 9 uses special format
                        lf_mf_code = int(lf_mf_freq) & 0xFFFF
                        
                        b2_tail = (tp_on << 4) | 0x09
                        # For variant 9: block 3 contains VHF code + method indicator
                        b3_val = (vhf_code << 8) | ((lf_mf_code >> 8) & 0xFF)
                        # Note: full LF/MF freq would need special handling, simplified here
                    except:
                        b2_tail = (tp_on << 4) | 0x09
                        b3_val = (205 << 8) | 205
                    
                    self.eon_mapped_idx += 1
                    if self.eon_mapped_idx >= len(lf_mf_mapped):
                        # Done with LF/MF - check for AF list next
                        af_list = service.get('af_list', '')
                        afs = [f.strip() for f in af_list.split(',') if f.strip()]
                        if afs:
                            self.eon_variant = 4
                            self.eon_af_idx = -1
                        else:
                            self.eon_variant = 13
                else:
                    # No LF/MF mapped - move to AF list or PTY+TA
                    af_list = service.get('af_list', '')
                    afs = [f.strip() for f in af_list.split(',') if f.strip()]
                    if afs:
                        self.eon_variant = 4
                        self.eon_af_idx = -1
                        b2_tail = (tp_on << 4) | 0x04
                        b3_val = 0xE0E0
                    else:
                        self.eon_variant = 13
                        b2_tail = (tp_on << 4) | 0x0D
                        b3_val = 0xE0E0

            # Variant 4: AF(ON) - Method A: Send ON frequencies without TN
            elif self.eon_variant == 4:
                # Get other network's frequencies (ALL frequencies in af_list are ON freqs)
                af_list = service.get('af_list', '')
                afs = [f.strip() for f in af_list.split(',') if f.strip()]

                b2_tail = (tp_on << 4) | 0x04  # Variant 4

                if not afs:
                    # No AFs - send filler and skip
                    b3_val = (205 << 8) | 205  # Filler codes (not count codes)
                    self.eon_variant = 13  # Next variant: PTY+TA
                else:
                    if self.eon_af_idx == -1:
                        # First transmission: count code + first frequency (Method A format)
                        b3_val = ((224 + len(afs)) << 8) | self.freq_code(afs[0])
                        
                        # If only 1 AF, skip directly to PTY+TA (per AF Method A examples)
                        if len(afs) == 1:
                            self.eon_variant = 13  # Done with AFs
                        else:
                            self.eon_af_idx = 1  # More AFs to send, start at index 1
                    elif self.eon_af_idx >= 0 and self.eon_af_idx < len(afs):
                        # Subsequent transmissions: send two frequencies at a time (Method A)
                        f1 = self.freq_code(afs[self.eon_af_idx])
                        f2 = self.freq_code(afs[self.eon_af_idx + 1]) if self.eon_af_idx + 1 < len(afs) else 205
                        b3_val = (f1 << 8) | f2
                        self.eon_af_idx += 2

                        # Check if we've sent all AFs
                        if self.eon_af_idx >= len(afs):
                            self.eon_variant = 13  # Next variant: PTY+TA
                    else:
                        # All AFs sent, shouldn't reach here but move to next variant
                        b3_val = (205 << 8) | 205
                        self.eon_variant = 13

            # Variant 13: PTY(ON) + TA
            elif self.eon_variant == 13:
                b2_tail = (tp_on << 4) | 0x0D  # Variant 13
                pty_on = service.get('pty', 0) & 0x1F
                ta_on = service.get('ta', 0) & 1

                b3_val = (pty_on << 11) | (ta_on << 10)

                # After PTY+TA, move to next service or back to PS
                self.eon_service_idx += 1
                if self.eon_service_idx >= len(eon_services):
                    self.eon_service_idx = 0
                self.eon_variant = 0  # Start with PS for next service
                self.eon_ps_seg = 0
                self.eon_af_idx = -1
                self.eon_mapped_idx = 0

            else:
                # Invalid variant - reset
                b2_tail = (tp_on << 4)
                b3_val = 0xE0E0
                self.eon_variant = 0
                self.eon_ps_seg = 0

            # Regular EON uses 14A format (variants 0-13)
            # 14B is only used for TA burst
            return RDSHelper.get_group_bits(14, 0, b2_tail, b3_val, pi_on)  # 14A format

        elif g_type == 15 and (not state["scheduler_auto"] or state["en_lps"]):
            raw = self.get_text("ps_long_32")
            lps_centered = state['lps_centered']
            if raw != self.last_lps_content: self.last_lps_content, self.lps_ptr = raw, 0
            if not self.lps_sequence or raw != self.lps_sequence[0][1].strip() or not hasattr(self, 'last_lps_centered') or self.last_lps_centered != lps_centered:
                self.lps_sequence = self.parse_smart(raw, 32, lps_centered)
                self.last_lps_centered = lps_centered
            dur, txt = self.lps_sequence[self.lps_seq_idx % len(self.lps_sequence)]
            
            monitor_data["lps"] = txt + ('\r' if state['lps_cr'] else '')

            if (time.time() - self.lps_seq_start_time) >= dur:
                self.lps_seq_idx += 1
                self.lps_seq_start_time, self.lps_ptr = time.time(), 0
                dur, txt = self.lps_sequence[self.lps_seq_idx % len(self.lps_sequence)]
            if state['lps_cr']:
                # Strip trailing spaces, append CR, then encode to UTF-8 (Group 15 supports UTF-8)
                txt_stripped = txt.rstrip()
                lps_txt = (txt_stripped + '\r').encode('utf-8')
                if len(lps_txt) < 4:
                    lps_txt = lps_txt.ljust(4, b'\x00')
                # Reset and restart from seg 0 once all needed segments have been sent
                # (mirrors RT CR behaviour: reset pointer then send seg 0 in the same call)
                segs_needed = (len(lps_txt) + 3) // 4  # ceiling division, 1-8
                if self.lps_ptr >= segs_needed:
                    self.lps_ptr = 0
            else:
                lps_txt = txt.encode('utf-8').ljust(32, b'\x00')[:32]
            seg = self.lps_ptr % 8
            self.lps_ptr += 1
            # Pad segment data if needed
            while len(lps_txt) < (seg + 1) * 4:
                lps_txt += b'\x00'
            return RDSHelper.get_group_bits(15, g_ver, seg, (lps_txt[seg*4]<<8)|lps_txt[seg*4+1], (lps_txt[seg*4+2]<<8)|lps_txt[seg*4+3])

        elif g_type == 10 and (not state["scheduler_auto"] or state["en_ptyn"]):
            raw = self.get_text("ptyn")
            if raw != self.last_ptyn_content: self.last_ptyn_content, self.ptyn_ptr = raw, 0
            if not self.ptyn_sequence or raw != self.ptyn_sequence[0][1].strip(): 
                self.ptyn_sequence = self.parse_smart(raw, 8, state['ptyn_centered'])
            dur, txt = self.ptyn_sequence[self.ptyn_seq_idx % len(self.ptyn_sequence)]
            
            monitor_data["ptyn"] = txt

            if (time.time() - self.ptyn_seq_start_time) >= dur:
                self.ptyn_seq_idx += 1
                self.ptyn_seq_start_time, self.ptyn_ptr = time.time(), 0
                dur, txt = self.ptyn_sequence[self.ptyn_seq_idx % len(self.ptyn_sequence)]
            txt = txt.ljust(8)
            # Convert PTYN text to RDS bytes (IEC 62106-4:2018)
            txt_bytes = text_to_rds_bytes(txt)
            seg = self.ptyn_ptr % 2
            self.ptyn_ptr += 1
            return RDSHelper.get_group_bits(10, g_ver, seg, (txt_bytes[seg*4]<<8)|txt_bytes[seg*4+1], (txt_bytes[seg*4+2]<<8)|txt_bytes[seg*4+3])
            
        elif g_type == 1:
            # In manual mode, transmit if explicitly scheduled; in auto mode, check en_id or en_pin flag
            if not state["scheduler_auto"] or state["en_id"] or state.get("en_pin", 0):
                # Group 1A: ECC/LIC in Block 3, PIN in Block 4 (if enabled)
                # Cycle between ECC (variant 0) and LIC (variant 3) in Block 3
                variants = [('ecc', 0), ('lic', 3)] if state.get("en_id", 1) else [('ecc', 0)]

                # Cycle through variants every 2 seconds
                variant_idx = int(time.time() / 2) % len(variants)
                variant_type, variant_code = variants[variant_idx]

                # Block 3: ECC or LIC with variant code in upper nibble
                ecc_lic_value = int(state['ecc' if variant_type == 'ecc' else 'lic'], 16) & 0xFF
                block3 = (variant_code << 12) | ecc_lic_value

                # Block 4: PIN if enabled, otherwise 0
                block4 = 0
                if get_effective_value("en_pin"):
                    # Programme Item Number (PIN) format - RDS Standard
                    # Block 4 contains 16-bit PIN: day (5 bits, MSB) + hour (5 bits) + minute (6 bits, LSB)
                    day = int(get_effective_value("pin_day")) & 0x1F  # 5 bits (0-31)
                    hour = int(get_effective_value("pin_hour")) & 0x1F  # 5 bits (0-23)
                    minute = int(get_effective_value("pin_minute")) & 0x3F  # 6 bits (0-59)

                    # Pack PIN into 16-bit value: [day:5][hour:5][minute:6]
                    block4 = (day << 11) | (hour << 6) | minute

                return RDSHelper.get_group_bits(1, g_ver, 0, block3, block4)

        elif g_type == 12 and (not state["scheduler_auto"] or state.get("en_dab")):
            # Group 12A: ODA data for DAB linkage (Ensemble table)
            # Per EN 301 700 spec section 5.3.3
            dab_ch = state.get("dab_channel", "12B")
            freq_mhz = {
                "5A": 174.928, "5B": 176.640, "5C": 178.352, "5D": 180.064,
                "6A": 181.936, "6B": 183.648, "6C": 185.360, "6D": 187.072,
                "7A": 188.928, "7B": 190.640, "7C": 192.352, "7D": 194.064,
                "8A": 195.936, "8B": 197.648, "8C": 199.360, "8D": 201.072,
                "9A": 202.928, "9B": 204.640, "9C": 206.352, "9D": 208.064,
                "10A": 209.936, "10B": 211.648, "10C": 213.360, "10D": 215.072, "10N": 210.096,
                "11A": 216.928, "11B": 218.640, "11C": 220.352, "11D": 222.064, "11N": 217.088,
                "12A": 223.936, "12B": 225.648, "12C": 227.360, "12D": 229.072, "12N": 224.096,
                "13A": 230.784, "13B": 232.496, "13C": 234.208, "13D": 235.776, "13E": 237.488, "13F": 239.200,
            }.get(dab_ch, 225.648)
            
            # Frequency field: 18-bit value = freq_kHz / 16
            freq_khz = int(freq_mhz * 1000)
            freq_code = freq_khz // 16  # 18-bit value
            
            # Get configurable DAB parameters
            mode = state.get("dab_mode", 1) & 0x03  # 2-bit mode
            es_flag = state.get("dab_es_flag", 0) & 0x01  # E/S flag: 0=ensemble, 1=service
            
            if es_flag == 0:
                # Ensemble table format (E/S = 0)
                # Block 2 Tail (5 bits): freq upper 2 bits (4:3) + mode (2:1) + E/S flag (0)
                freq_upper_2 = (freq_code >> 16) & 0x03
                b2_tail = (freq_upper_2 << 3) | (mode << 1) | es_flag
                
                # Block 3: frequency bits 15-0
                b3_val = freq_code & 0xFFFF
                
                # Block 4: EId (Ensemble ID)
                eid_str = state.get("dab_eid", "CE15")
                try:
                    eid = int(eid_str, 16) & 0xFFFF
                except:
                    eid = 0xCE15  # Default fallback
                b4_val = eid
            else:
                # Service table format (E/S = 1)
                variant = state.get("dab_variant", 0) & 0x0F  # 4-bit variant code
                
                # Block 2 Tail (5 bits): variant (4:1) + E/S flag (0)
                b2_tail = (variant << 1) | es_flag
                
                # Block 3: Info block (depends on variant)
                if variant == 0:
                    # Variant 0: Ensemble information - EId in info block
                    eid_str = state.get("dab_eid", "CE15")
                    try:
                        b3_val = int(eid_str, 16) & 0xFFFF
                    except:
                        b3_val = 0xCE15
                elif variant == 1:
                    # Variant 1: Linkage information (Rfa, LA, S/H, ILS, LSN)
                    # For now, use default values - could be expanded with more UI controls
                    # Format: Rfa(1) LA(1) S/H(1) ILS(1) LSN(12)
                    b3_val = 0x0000  # Default: all flags off, LSN=0
                else:
                    b3_val = 0x0000  # Other variants not implemented
                
                # Block 4: SId (Service ID)
                sid_str = state.get("dab_sid", "0000")
                try:
                    b4_val = int(sid_str, 16) & 0xFFFF
                except:
                    b4_val = 0x0000  # Default fallback
            
            return RDSHelper.get_group_bits(12, 0, b2_tail, b3_val, b4_val)

        elif g_type == 8:
            # Group 8A: Traffic Message Channel (TMC) - not implemented
            # Send PS group instead of TMC to prevent blanks (TMC requires complex message structure)
            raw = self.get_text("ps_dynamic") or "RDS_PRO "
            txt = raw.ljust(8)[:8]
            txt_bytes = text_to_rds_bytes(txt)
            seg = self.ps_ptr % 4
            self.ps_ptr += 1
            tail = (get_effective_value("ta")<<4)|(get_effective_value("ms")<<3)|([state['di_dyn'],state['di_comp'],state['di_head'],state['di_stereo']][seg]<<2)|seg
            return RDSHelper.get_group_bits(0, 0, tail, 0xE0E0, (txt_bytes[seg*2]<<8)|txt_bytes[seg*2+1])

        # Group not implemented - send PS group to prevent recursion and blanks
        # NOTE: Pointer was already incremented at line 1295
        raw = self.get_text("ps_dynamic") or "RDS_PRO "
        txt = raw.ljust(8)[:8]
        txt_bytes = text_to_rds_bytes(txt)
        seg = self.ps_ptr % 4
        self.ps_ptr += 1
        tail = (get_effective_value("ta")<<4)|(get_effective_value("ms")<<3)|([state['di_dyn'],state['di_comp'],state['di_head'],state['di_stereo']][seg]<<2)|seg
        return RDSHelper.get_group_bits(0, 0, tail, 0xE0E0, (txt_bytes[seg*2]<<8)|txt_bytes[seg*2+1])

# --- RDS2 DATA GENERATOR ---
class RDS2Generator:
    """
    Pure Python RDS2 File Transfer (RFT) implementation
    Based on RDS2 specification with ODA AID 0xFF7F
    """
    # RDS2 ODA Application ID for file transfer
    ODA_AID_RFT = 0xFF7F
    
    def __init__(self):
        self.logo_data = None
        self.logo_size = 0
        self.logo_loaded = False
        
        # File transfer state per stream (1, 2, 3)
        # All streams send the same pipe (0) so receiver sees one coherent file
        self.channel = [0, 0, 0]  # Pipe/channel number per stream (all pipe 0)
        self.file_version = [0, 0, 0]  # File version (0-7)
        self.file_id = [0, 0, 0]  # File ID (0-63)
        self.toggle = [0, 0, 0]  # Toggle bit
        self.segment_idx = [0, 0, 0]  # Current segment being transmitted
        self.num_segments = [0, 0, 0]  # Total segments
        self.rft_state = [0, 0, 0]  # State machine (0-49)
        self.use_crc = True  # Enable CRC
        self.crc_value = [0, 0, 0]  # Calculated CRC
        
    def load_logo(self, logo_path):
        """Load station logo from file for RDS2 file transfer"""
        if not logo_path or not os.path.exists(logo_path):
            self.logo_data = None
            self.logo_size = 0
            self.logo_loaded = False
            return False
        
        try:
            with open(logo_path, 'rb') as f:
                self.logo_data = bytearray(f.read())
                self.logo_size = len(self.logo_data)
                
                # Pad to multiple of 5 bytes (segment size)
                if self.logo_size % 5 != 0:
                    padding = 5 - (self.logo_size % 5)
                    self.logo_data.extend([0] * padding)
                
                # Calculate segments and CRC
                for i in range(3):
                    self.num_segments[i] = len(self.logo_data) // 5
                    self.segment_idx[i] = 0
                    self.crc_value[i] = self._calculate_crc16(self.logo_data)
                
                self.logo_loaded = True
                # print(f"[RDS2] Loaded logo: {logo_path} ({self.logo_size} bytes, {self.num_segments[0]} segments, CRC: 0x{self.crc_value[0]:04X})")
                return True
        except Exception as e:
            # print(f"[RDS2] Failed to load logo: {e}")
            pass
            self.logo_data = None
            self.logo_size = 0
            self.logo_loaded = False
            return False
    
    def _calculate_crc16(self, data):
        """Calculate CRC-16 matching RDS2 C reference implementation"""
        crc = 0xFFFF
        for i in range(self.logo_size):  # Only use actual file size, not padded
            crc = ((crc >> 8) | (crc << 8)) & 0xFFFF
            crc ^= data[i]
            crc ^= (crc & 0xFF) >> 4
            crc ^= (crc << 8) << 4
            crc ^= ((crc & 0xFF) << 4) << 1
            crc &= 0xFFFF
        return crc ^ 0xFFFF
    
    def get_logo_info(self):
        """Get information about loaded logo"""
        if self.logo_loaded and self.logo_data:
            return {
                "loaded": True,
                "size": self.logo_size,
                "segments": self.num_segments[0],
                "crc": self.crc_value[0]
            }
        return {"loaded": False, "size": 0, "segments": 0, "crc": 0}
    
    def get_rds2_group_bits(self, stream_num):
        """
        Generate RDS2 group bits for stream 1, 2, or 3
        Implements full RFT protocol with metadata, CRC, and file data
        """
        if stream_num < 1 or stream_num > 3:
            return [0] * 104
        
        idx = stream_num - 1
        
        if not self.logo_loaded or not self.logo_data:
            # No logo: send test pattern
            blocks = self._get_test_pattern(idx)
            return self._blocks_to_bits(blocks)
        
        # State machine matching C code: 0, 1, 2, 3 = metadata/CRC, 4-49 = file data
        state = self.rft_state[idx]
        
        if state == 0 or state == 2:
            # Variant 0: File metadata
            blocks = self._get_variant_0(idx)
        elif state == 1 or state == 3:
            # Variant 1: CRC information
            blocks = self._get_variant_1(idx)
        else:
            # File data transmission
            blocks = self._get_file_data(idx)
        
        # Advance state
        self.rft_state[idx] = (self.rft_state[idx] + 1) % 50
        
        # Convert 4 blocks to RDS group bits
        return self._blocks_to_bits(blocks)
    
    def _get_variant_0(self, idx):
        """RFT Variant 0: File metadata (exact C reference format)"""
        blocks = [0, 0, 0, 0]
        
        # Block 0: Function header (bit 15) + pipe number (bits 0-3)
        blocks[0] = (1 << 15)  # Function header
        blocks[0] |= (self.channel[idx] & 0x0F)  # Pipe number
        
        # Block 1: ODA Application ID for file transfer
        blocks[1] = self.ODA_AID_RFT
        
        # Block 2: Variant code (0) + CRC flag + version + file ID + file size upper
        blocks[2] = (0 & 0x0F) << 12  # Variant 0
        blocks[2] |= (1 if self.use_crc else 0) << 11  # CRC flag
        blocks[2] |= (self.file_version[idx] & 0x07) << 8  # File version
        blocks[2] |= (self.file_id[idx] & 0x3F) << 2  # File ID
        blocks[2] |= (self.logo_size >> 16) & 0x03  # File size upper 2 bits
        
        # Block 3: File size lower 16 bits
        blocks[3] = self.logo_size & 0xFFFF
        
        # Debug output (only once per stream)
        # if self.rft_state[idx] == 0:
        #     print(f"[RDS2-{idx+1}] Variant 0: Size={self.logo_size}, CRC=0x{self.crc_value[idx]:04X}, Blocks={[f'{b:04X}' for b in blocks]}")
        
        return blocks
    
    def _get_variant_1(self, idx):
        """RFT Variant 1: CRC information (exact C reference format)"""
        blocks = [0, 0, 0, 0]
        
        # Block 0: Function header + pipe number
        blocks[0] = (1 << 15)
        blocks[0] |= (self.channel[idx] & 0x0F)
        
        # Block 1: ODA AID
        blocks[1] = self.ODA_AID_RFT
        
        # Block 2: Variant code (1) + CRC mode (0 = entire file) + chunk address (0)
        blocks[2] = (1 & 0x0F) << 12  # Variant 1
        blocks[2] |= (0 & 0x07) << 9  # CRC mode 0 (entire file)
        blocks[2] |= 0 & 0x1FF  # Chunk address 0
        
        # Block 3: CRC-16 value
        blocks[3] = self.crc_value[idx] & 0xFFFF
        
        # Debug output (only once per stream)
        # if self.rft_state[idx] == 1:
        #     print(f"[RDS2-{idx+1}] Variant 1: CRC=0x{self.crc_value[idx]:04X}, Blocks={[f'{b:04X}' for b in blocks]}")
        
        return blocks
    
    def _get_file_data(self, idx):
        """RFT File Data: 5 bytes per group (exact C reference format)"""
        blocks = [0, 0, 0, 0]
        
        # Block 0: Function header (bits 12-15) + pipe (bits 8-11) + toggle (bit 7) + segment addr upper (bits 0-6)
        blocks[0] = (2 << 12)  # Function header
        blocks[0] |= (self.channel[idx] & 0x0F) << 8  # Pipe number
        blocks[0] |= (self.toggle[idx] & 0x01) << 7  # Toggle bit
        blocks[0] |= (self.segment_idx[idx] >> 8) & 0x7F  # Segment address upper 7 bits
        
        # Block 1: Segment address lower 8 bits + first data byte
        blocks[1] = (self.segment_idx[idx] & 0xFF) << 8
        
        # Get 5 bytes of file data
        seg_offset = self.segment_idx[idx] * 5
        if seg_offset + 5 <= len(self.logo_data):
            blocks[1] |= self.logo_data[seg_offset + 0]
            blocks[2] = (self.logo_data[seg_offset + 1] << 8) | self.logo_data[seg_offset + 2]
            blocks[3] = (self.logo_data[seg_offset + 3] << 8) | self.logo_data[seg_offset + 4]
        else:
            blocks[1] |= 0
            blocks[2] = 0
            blocks[3] = 0
        
        # Advance segment counter
        self.segment_idx[idx] += 1
        if self.segment_idx[idx] >= self.num_segments[idx]:
            self.segment_idx[idx] = 0
            self.toggle[idx] ^= 1  # Toggle bit flips each cycle
        
        # Debug output (only first data block per stream)
        # if self.rft_state[idx] == 4:
        #     print(f"[RDS2-{idx+1}] File Data: Seg=0, Toggle={self.toggle[idx]}, Blocks={[f'{b:04X}' for b in blocks]}")
        
        return blocks
    
    def _get_test_pattern(self, idx):
        """Generate test pattern when no logo loaded"""
        counter = self.rft_state[idx]
        self.rft_state[idx] = (self.rft_state[idx] + 1) % 65536
        
        blocks = [
            0x8000 | (idx & 0x0F),  # Function header + pipe
            self.ODA_AID_RFT,        # ODA AID
            counter & 0xFFFF,        # Counter
            (~counter) & 0xFFFF      # Inverted counter
        ]
        
        return blocks
    
    def _blocks_to_bits(self, blocks):
        """Convert 4 RDS2 blocks to 104 bits"""
        return RDSHelper.rds2_blocks_to_bits(blocks[0], blocks[1], blocks[2], blocks[3])

# --- DSP ENGINE ---
class RDSDSP:
    # Number of bits to keep pre-generated in the queue (~100 ms of RDS data).
    # Kept short so urgent groups (eRT RT+ clears) reach the wire within ~100 ms.
    # The fill thread maintains this level; the audio callback never calls the scheduler.
    _QUEUE_TARGET = 1200

    def __init__(self):
        self.sched = RDSScheduler()
        self.p_rds, self.p_pilot, self.bit_clock, self.last_bit = 0.0, 0.0, 0.0, 0
        self.bit_queue = collections.deque()
        
        # RDS2 generator and oscillator phases
        self.rds2_gen = RDS2Generator()
        self.p_rds2_1, self.p_rds2_2, self.p_rds2_3 = 0.0, 0.0, 0.0  # Phases for 66.5, 71.25, 76 kHz
        self.rds2_bit_clock = [0.0, 0.0, 0.0]
        self.rds2_last_bit = [0, 0, 0]
        self.rds2_bit_queue = [collections.deque(), collections.deque(), collections.deque()]
        self.rds2_zi = [np.zeros(2048), np.zeros(2048), np.zeros(2048)]

        # ARI (Autofahrer-Rundfunk-Information) phase accumulators
        # ARI is a SEPARATE 57 kHz carrier (sits in RDS biphase spectral notch)
        self.p_ari = 0.0     # Phase for 57 kHz ARI carrier (SK - Sender Kennung)
        self.p_ari_bk = 0.0  # Phase for BK modulation (region identifier, varies 23.75-53.977 Hz)
        self.p_ari_dk = 0.0  # Phase for DK modulation (announcement identifier, 125 Hz)

        # Load RDS2 logo if available in state
        if state.get("rds2_logo_path"):
            try:
                self.rds2_gen.load_logo(state["rds2_logo_path"])
                # print(f"[RDS2] Loaded logo: {state['rds2_logo_path']}")
            except Exception as e:
                # print(f"[RDS2] Failed to load logo: {e}")
                pass
        
        # 2049-tap Kaiser β=8 lowpass at 2×BITRATE (2.375 kHz).
        # 301 taps at 192 kHz gave a ~10 kHz transition band, leaving sinc sidelobes
        # visible on a spectrum analyser.  2049 taps narrows the transition band to
        # ~1.4 kHz, pushing sidelobes well below the noise floor of any FM receiver.
        # Stopband attenuation remains ~80 dB (set by Kaiser β, not tap count).
        self.taps = dsp_signal.firwin(2049, BITRATE * 2.0, fs=SAMPLE_RATE, window=('kaiser', 8.0))
        self.zi = np.zeros(2048)
        # Pre-fill the queue synchronously so audio starts immediately with data ready.
        self._last_good_bits = None  # Cache of last successfully generated group bits
        self._urgent_bits = collections.deque()  # Front-of-queue injections (e.g. eRT RT+ clears)
        self._prefill()
        # Background thread keeps the queue topped up without touching the audio hot-path.
        threading.Thread(target=self._bit_fill_loop, daemon=True).start()

    def _prefill(self):
        while len(self.bit_queue) < self._QUEUE_TARGET:
            try:
                bits = self.sched.next()
                self._last_good_bits = list(bits)
                self.bit_queue.extend(bits)
            except Exception as e:
                print(f"[RDS] prefill exception: {e}", flush=True)
                fallback = self._last_good_bits or list(RDSHelper.get_group_bits(1, 0, 0, 0, 0))
                self.bit_queue.extend(fallback)

    def _bit_fill_loop(self):
        """Non-real-time thread: keeps bit_queue near _QUEUE_TARGET so the
        audio callback never has to call the scheduler directly."""
        while state["running"]:
            # Check if RDS2 logo needs reloading
            if state.get("rds2_logo_reload", False):
                logo_path = state.get("rds2_logo_path", "")
                if logo_path:
                    try:
                        self.rds2_gen.load_logo(logo_path)
                        # print(f"[RDS2] Reloaded logo: {logo_path}")
                    except Exception as e:
                        # print(f"[RDS2] Failed to reload logo: {e}")
                        pass
                state["rds2_logo_reload"] = False
            
            # Time-bound each fill burst to ≤3 ms so the audio callback always
            # wins the GIL within its 10.67 ms window.  A burst_counter=16 PS
            # update would otherwise monopolise the GIL for ~3–5 ms.
            t0 = time.perf_counter()
            while len(self.bit_queue) < self._QUEUE_TARGET and state["running"]:
                try:
                    bits = self.sched.next()
                    self._last_good_bits = list(bits)
                    self.bit_queue.extend(bits)
                except Exception as e:
                    print(f"[RDS] fill-thread exception: {e}", flush=True)
                    fallback = self._last_good_bits or list(RDSHelper.get_group_bits(1, 0, 0, 0, 0))
                    self.bit_queue.extend(fallback)
                # Explicit GIL yield after every group.  When burst_counter fires
                # (16 groups in rapid succession on PS content change), this lets
                # PortAudio's callback thread acquire the GIL between each group
                # rather than waiting up to 3 ms for the time-bound to fire.
                time.sleep(0)
                if time.perf_counter() - t0 > 0.020:
                    break   # hard cap: exit inner loop after 20 ms regardless
            time.sleep(0.005)   # yield GIL for 5 ms between top-up bursts

    def process_frame(self, outdata, frames, indata=None):
        lvl_pilot, lvl_rds = (state["pilot_level"]/100.0), (state["rds_level"]/100.0)
        rds_freq = float(state.get("rds_freq", 57000))

        # Genlock only valid when RDS is 3x pilot (57kHz)
        use_genlock = state["genlock"] and indata is not None and len(indata) == frames and abs(rds_freq - 3*PILOT_FREQ) < 1e-3
        if use_genlock:
             try:
                 mpx_in = indata[:, 0]
                 target_bin = int((PILOT_FREQ / SAMPLE_RATE) * frames)
                 spectrum = np.fft.fft(mpx_in)
                 phase_19k = np.angle(spectrum[target_bin])
                 self.p_rds = (phase_19k * 3.0) + np.deg2rad(state["genlock_offset"])
             except: pass

        # Vectorized biphase-mark (FM0) generation — no Python loop over samples.
        # cum[i] = accumulated bit-clock after sample i; integer part = bits consumed so far.
        dr = BITRATE / SAMPLE_RATE
        cum = self.bit_clock + np.arange(1, frames + 1) * dr
        n_bits_at = np.floor(cum).astype(np.int32)   # bits consumed up to each sample
        n_bits_total = int(n_bits_at[-1])

        # Queue is maintained by _bit_fill_loop; emergency fallback only.
        # IMPORTANT: never use Group 0A as padding — it would overwrite the PS display
        # on the decoder with garbage characters. Repeat the last good group instead.
        if len(self.bit_queue) < n_bits_total:
            print(f"[DSP] queue underrun ({len(self.bit_queue)}/{n_bits_total})", flush=True)
            fallback = self._last_good_bits or list(RDSHelper.get_group_bits(1, 0, 0, 0, 0))
            while len(self.bit_queue) < n_bits_total:
                self.bit_queue.extend(fallback)

        # Build prefix-XOR array: xor_prefix[k] = XOR of the first k bits from queue.
        # xor_prefix[0] = 0 (no bits consumed yet).
        xor_prefix = np.zeros(n_bits_total + 1, dtype=np.int32)
        if n_bits_total > 0:
            raw = np.fromiter(
                (self.bit_queue.popleft() for _ in range(n_bits_total)),
                dtype=np.int32, count=n_bits_total)
            xor_prefix[1:] = np.cumsum(raw) & 1

        # State at each sample: last_bit XOR accumulated XOR up to that sample.
        states = (self.last_bit ^ xor_prefix[n_bits_at]) & 1
        self.last_bit ^= int(xor_prefix[n_bits_total])
        self.bit_clock = float(cum[-1] - n_bits_total)

        # +1/-1 carrier; invert the second half of each bit period (FM0 encoding).
        bb = np.where(states, 1.0, -1.0)
        frac = cum - n_bits_at
        bb = np.where(frac >= 0.5, -bb, bb)
            
        shaped, self.zi = dsp_signal.lfilter(self.taps, 1.0, bb, zi=self.zi)
        t = np.arange(frames) / SAMPLE_RATE
        
        # Normalise by π/4: the FIR-filtered Manchester biphase signal has a worst-case
        # peak of 4/π (≈1.27), so multiply by π/4 to make the stated % equal the true peak.
        # This mirrors the pilot, whose peak is exactly lvl_pilot (pure sine, amplitude 1).
        if lvl_rds > 0:
            rds_sig = shaped * (np.pi / 4.0) * np.sin(2 * np.pi * rds_freq * t + self.p_rds) * lvl_rds
        else:
            rds_sig = 0.0  # No signal when level is 0%

        pilot_sig = 0.0
        if not use_genlock and not (state.get("passthrough") and indata is not None):
             pilot_sig = np.sin(2 * np.pi * PILOT_FREQ * t + self.p_pilot) * lvl_pilot

             # Phase Lock: If pilot is active, lock RDS phase to pilot (RDS = 3 * Pilot)
             # Add π phase shift so sum of 19kHz and 57kHz has minimum amplitude
             if lvl_pilot > 0:
                 self.p_rds = (self.p_pilot * 3.0 + np.pi) % (2 * np.pi)

             self.p_pilot = (self.p_pilot + 2 * np.pi * PILOT_FREQ * frames / SAMPLE_RATE) % (2 * np.pi)

        # ARI (Autofahrer-Rundfunk-Information) - SEPARATE 57 kHz carrier with AM
        # RDS biphase encoding has spectral NULL at 57 kHz center, allowing ARI carrier to coexist there
        ari_sig = 0.0
        if state.get("en_ari", False):
            # ARI region frequencies (BK - Bereichs Kennung)
            ari_region_freqs = {
                "A": 23.75,   # Mecklenburg-WP, Bremen, Sachsen, BW (US zone)
                "B": 28.274,  # Schleswig-Holstein, Sachsen-Anhalt, Saarland
                "C": 34.926,  # Hamburg, Berlin, NRW, Bayern Nord
                "D": 39.583,  # Niedersachsen SE, Rheinland-Pfalz, Bayern Süd
                "E": 45.673,  # Niedersachsen West, Thüringen, BW Süd (FR zone)
                "F": 53.977   # Brandenburg, Hessen
            }

            ari_region = state.get("ari_region", "B")
            bk_freq = ari_region_freqs.get(ari_region, 28.274)

            # ARI carrier level (uses same level as RDS)
            ari_level = state.get("rds_level", 4.5) / 100.0

            # Modulation depths (as fractions, e.g., 60% = 0.6)
            bk_depth = state.get("ari_bk_level", 60) / 100.0
            dk_depth = state.get("ari_dk_level", 30) / 100.0

            # Determine if announcement (DK) is active
            ari_announcement_effective = dynamic_overrides.get("ari_announcement", state.get("ari_announcement", False))

            # Auto mode: tie to RDS TA+TP flags
            if state.get("ari_announcement_mode", "manual") == "rds_ta":
                tp_effective = get_effective_value("tp")
                ta_effective = get_effective_value("ta")
                ari_announcement_effective = (tp_effective == 1) and (ta_effective == 1)

            # Build ARI amplitude modulation envelope
            # AM formula: carrier * (1 + m_bk * sin(ωt) + m_dk * sin(ωt))
            ari_envelope = 1.0 + (bk_depth * np.sin(2 * np.pi * bk_freq * t + self.p_ari_bk))

            # Add DK (125 Hz announcement) if active
            if ari_announcement_effective:
                ari_envelope += dk_depth * np.sin(2 * np.pi * 125.0 * t + self.p_ari_dk)

            # Generate SEPARATE 57 kHz pure carrier with AM (sits in RDS spectral notch)
            # Phase-lock to pilot if pilot is active (57 kHz = 3 × 19 kHz + π)
            if lvl_pilot > 0 and not use_genlock:
                self.p_ari = (self.p_pilot * 3.0 + np.pi) % (2 * np.pi)
            else:
                self.p_ari = (self.p_ari + 2 * np.pi * 57000 * frames / SAMPLE_RATE) % (2 * np.pi)

            ari_sig = np.sin(2 * np.pi * 57000 * t + self.p_ari) * ari_envelope * ari_level

            # Update ARI modulation phase accumulators
            self.p_ari_bk = (self.p_ari_bk + 2 * np.pi * bk_freq * frames / SAMPLE_RATE) % (2 * np.pi)
            self.p_ari_dk = (self.p_ari_dk + 2 * np.pi * 125.0 * frames / SAMPLE_RATE) % (2 * np.pi)

        self.p_rds = (self.p_rds + 2 * np.pi * rds_freq * frames / SAMPLE_RATE) % (2 * np.pi)
        mixed = rds_sig + pilot_sig + ari_sig
        
        # Add RDS2 carriers if enabled (pure Python implementation)
        if state.get("en_rds2", False):
            num_carriers = int(state.get("rds2_num_carriers", 0))
            
            # RDS2 carrier frequencies (66.5, 71.25, 76 kHz)
            rds2_freqs = [66500, 71250, 76000]
            rds2_levels = [
                state.get("rds2_carrier1_level", 4.5) / 100.0,
                state.get("rds2_carrier2_level", 4.5) / 100.0,
                state.get("rds2_carrier3_level", 4.5) / 100.0
            ]
            
            # Generate each enabled RDS2 carrier
            for i in range(min(num_carriers, 3)):
                try:
                    # Get RDS2 bits from generator (stream 1, 2, 3)
                    # Fill queue if needed
                    if len(self.rds2_bit_queue[i]) < 104:
                        rds2_bits = self.rds2_gen.get_rds2_group_bits(i + 1)
                        self.rds2_bit_queue[i].extend(rds2_bits)
                    
                    # Generate RDS2 carrier using same biphase modulation as main RDS
                    dr = BITRATE / SAMPLE_RATE
                    cum = self.rds2_bit_clock[i] + np.arange(1, frames + 1) * dr
                    n_bits_at = np.floor(cum).astype(np.int32)
                    n_bits_total = int(n_bits_at[-1])
                    
                    # Fill queue if underrun
                    while len(self.rds2_bit_queue[i]) < n_bits_total:
                        rds2_bits = self.rds2_gen.get_rds2_group_bits(i + 1)
                        self.rds2_bit_queue[i].extend(rds2_bits)
                    
                    if len(self.rds2_bit_queue[i]) >= n_bits_total and n_bits_total > 0:
                        # Build prefix-XOR array
                        xor_prefix = np.zeros(n_bits_total + 1, dtype=np.int32)
                        raw = np.fromiter(
                            (self.rds2_bit_queue[i].popleft() for _ in range(n_bits_total)),
                            dtype=np.int32, count=n_bits_total)
                        xor_prefix[1:] = np.cumsum(raw) & 1
                        
                        # State and encoding
                        states = (self.rds2_last_bit[i] ^ xor_prefix[n_bits_at]) & 1
                        self.rds2_last_bit[i] ^= int(xor_prefix[n_bits_total])
                        self.rds2_bit_clock[i] = float(cum[-1] - n_bits_total)
                        
                        bb = np.where(states, 1.0, -1.0)
                        frac = cum - n_bits_at
                        bb = np.where(frac >= 0.5, -bb, bb)
                        
                        shaped, self.rds2_zi[i] = dsp_signal.lfilter(self.taps, 1.0, bb, zi=self.rds2_zi[i])
                        
                        # Apply carrier modulation with quadrature phase shifting
                        # Stream 1: 90°, Stream 2: 180°, Stream 3: 270°
                        phase_shifts = [np.pi/2, np.pi, 3*np.pi/2]
                        rds2_phase = [self.p_rds2_1, self.p_rds2_2, self.p_rds2_3][i]
                        
                        rds2_sig = shaped * (np.pi / 4.0) * np.sin(2 * np.pi * rds2_freqs[i] * t + rds2_phase + phase_shifts[i]) * rds2_levels[i]
                        mixed += rds2_sig
                        
                        # Update phase
                        if i == 0:
                            self.p_rds2_1 = (self.p_rds2_1 + 2 * np.pi * rds2_freqs[i] * frames / SAMPLE_RATE) % (2 * np.pi)
                        elif i == 1:
                            self.p_rds2_2 = (self.p_rds2_2 + 2 * np.pi * rds2_freqs[i] * frames / SAMPLE_RATE) % (2 * np.pi)
                        else:
                            self.p_rds2_3 = (self.p_rds2_3 + 2 * np.pi * rds2_freqs[i] * frames / SAMPLE_RATE) % (2 * np.pi)
                
                except Exception as e:
                    print(f"[RDS2] Carrier {i+1} exception: {e}", flush=True)
        
        # Channel routing based on output_channel setting
        output_channel = state.get("output_channel", "both")
        if output_channel == "left":
            # RDS on left channel only, silence on right
            stereo_out = np.column_stack((mixed, np.zeros_like(mixed)))
        elif output_channel == "right":
            # Silence on left, RDS on right channel only
            stereo_out = np.column_stack((np.zeros_like(mixed), mixed))
        else:  # "both" (default)
            # RDS on both channels (stereo)
            stereo_out = np.column_stack((mixed, mixed))

        if indata is not None and state["passthrough"] and indata.shape[1] == 2:
             outdata[:] = indata + stereo_out
        else:
             outdata[:] = stereo_out

    def callback_duplex(self, indata, outdata, frames, time, status):
        if status.output_underflow:
            print("[DSP] output underflow", flush=True)
        try:
            self.process_frame(outdata, frames, indata)
        except Exception as e:
            print(f"[DSP] callback exception: {e}", flush=True)

    def callback_output(self, outdata, frames, time, status):
        if status.output_underflow:
            print("[DSP] output underflow", flush=True)
        try:
            self.process_frame(outdata, frames, None)
        except Exception as e:
            print(f"[DSP] callback exception: {e}", flush=True)

def run_audio():
    import gc, sys
    gc.collect()                    # collect any cycles before freezing
    try:
        gc.freeze()                 # freeze all existing objects (Flask/SocketIO framework)
    except AttributeError:
        pass                        # gc.freeze() requires Python 3.7+
    gc.disable()                    # prevent GC pauses from dropping the callback deadline
    sys.setswitchinterval(0.001)    # 1 ms GIL slice — audio thread wins the GIL faster
    engine = RDSDSP()
    try:
        sd_in = state["device_in_idx"] if state["device_in_idx"] != -1 else None
        sd_out = state["device_out_idx"]
        
        if sd_in is not None:
             with sd.Stream(device=(sd_in, sd_out), samplerate=SAMPLE_RATE, blocksize=2048, channels=2, callback=engine.callback_duplex):
                 while state["running"]: sd.sleep(100)
        else:
             with sd.OutputStream(device=sd_out, samplerate=SAMPLE_RATE, blocksize=2048, channels=2, callback=engine.callback_output):
                 while state["running"]: sd.sleep(100)
    except Exception as e:
        print(f"Audio Error: {e}")
        state["running"] = False

# --- UI ---
@app.route('/')
def index():
    if not session.get('auth'): return redirect(url_for('login'))
    inputs, outputs = get_valid_devices()
    return render_template_string(UI_HTML, inputs=inputs, outputs=outputs, state=state, auto_start=auto_start, pty_list_rds=PTY_LIST_RDS, pty_list_rbds=PTY_LIST_RBDS, auth_config=auth_config, site_name=site_name, http_port=http_port, version=VERSION)

@app.route('/login', methods=['GET','POST'])
def login():
    msg = ""
    if request.method == 'POST':
        u = request.form.get('user', '')
        p = request.form.get('pass', '')
        if u == auth_config.get('user') and p == auth_config.get('pass'):
            session['auth'] = True
            return redirect(url_for('index'))
        else:
            msg = "Invalid credentials"
    return render_template_string(LOGIN_HTML, msg=msg, user=auth_config.get('user',''), site_name=site_name, version=VERSION)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/settings', methods=['POST'])
def update_settings():
    if not session.get('auth'): return {"ok": False, "error": "unauthorized"}, 401
    data = request.get_json(silent=True) or {}
    changed = False

    if 'auto_start' in data:
        global auto_start
        auto_start = bool(data['auto_start'])
        changed = True

    user = data.get('user')
    if isinstance(user, str) and user.strip():
        auth_config['user'] = user.strip()
        changed = True

    password = data.get('password')
    if isinstance(password, str) and password.strip():
        auth_config['pass'] = password.strip()
        changed = True

    site = data.get('site_name')
    if isinstance(site, str):
        global site_name
        site_name = site.strip() if site.strip() else 'Secure Login'
        changed = True

    if changed: save_config()
    return {"ok": True}

@app.route('/uecp_settings', methods=['GET', 'POST'])
def uecp_settings_route():
    global _uecp_tcp_server, _uecp_ws_client
    if not session.get('auth'): return jsonify({"ok": False, "error": "unauthorized"}), 401

    if request.method == 'GET':
        return jsonify({
            "uecp_enabled":    state.get("uecp_enabled", False),
            "uecp_port":       state.get("uecp_port", 4001),
            "uecp_host":       state.get("uecp_host", "0.0.0.0"),
            "uecp_psn":        state.get("uecp_psn", 0),
            "uecp_dsn":        state.get("uecp_dsn", 0),
            "uecp_ws_enabled": state.get("uecp_ws_enabled", False),
            "uecp_ws_url":     state.get("uecp_ws_url", "ws://127.0.0.1/pacific"),
            "tcp_running":     _uecp_tcp_server is not None,
            "ws_running":      _uecp_ws_client is not None,
        })

    data = request.get_json(silent=True) or {}
    state["uecp_enabled"] = bool(data.get("uecp_enabled", False))
    try:
        state["uecp_port"] = max(1, min(65535, int(data.get("uecp_port", 4001))))
    except (ValueError, TypeError):
        state["uecp_port"] = 4001
    state["uecp_host"] = str(data.get("uecp_host", "0.0.0.0")).strip() or "0.0.0.0"
    try:
        state["uecp_psn"] = max(0, min(255, int(data.get("uecp_psn", 0))))
    except (ValueError, TypeError):
        state["uecp_psn"] = 0
    try:
        state["uecp_dsn"] = max(0, min(255, int(data.get("uecp_dsn", 0))))
    except (ValueError, TypeError):
        state["uecp_dsn"] = 0
    state["uecp_ws_enabled"] = bool(data.get("uecp_ws_enabled", False))
    state["uecp_ws_url"] = str(data.get("uecp_ws_url", "")).strip() or "ws://127.0.0.1/pacific"
    save_config()

    # Stop any existing TCP server and WS client
    if _uecp_tcp_server is not None:
        try:
            _uecp_tcp_server.stop()
        except Exception:
            pass
        _uecp_tcp_server = None
    if _uecp_ws_client is not None:
        try:
            _uecp_ws_client.stop()
        except Exception:
            pass
        _uecp_ws_client = None

    tcp_enabled = state["uecp_enabled"]
    ws_enabled  = state["uecp_ws_enabled"]

    if not tcp_enabled and not ws_enabled:
        return jsonify({"ok": True, "status": "UECP disabled"})

    try:
        from uecp_server import UECPStateHandler, UECPTCPServer, UECPWebSocketClient

        def _uecp_save():
            socketio.emit('state_update', {
                'custom_oda_list': state.get('custom_oda_list', '[]'),
                'custom_groups':   state.get('custom_groups',   '[]'),
            })
        def _uecp_restore():
            save_config()
            socketio.emit('state_update', {
                'custom_oda_list': state.get('custom_oda_list', '[]'),
                'custom_groups':   state.get('custom_groups',   '[]'),
            })

        # Shared handler — both TCP and WS connections pool into the same
        # client count so that snapshot/restore triggers correctly.
        handler = UECPStateHandler(state, _uecp_save, _uecp_restore)
        status_parts = []

        if tcp_enabled:
            srv = UECPTCPServer(state["uecp_host"], state["uecp_port"], handler)
            srv.start()
            _uecp_tcp_server = srv
            status_parts.append(f"TCP listening on {srv.host}:{srv.port}")

        if ws_enabled:
            wsc = UECPWebSocketClient(state["uecp_ws_url"], handler)
            wsc.start()
            _uecp_ws_client = wsc
            status_parts.append(f"WS client → {wsc.url}")

        return jsonify({"ok": True, "status": "; ".join(status_parts)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/fetch-json-structure', methods=['POST'])
def fetch_json_structure():
    """Fetch JSON from URL and return available fields and sample data."""
    if not session.get('auth'): return {"ok": False, "error": "unauthorized"}, 401
    data = request.get_json(silent=True) or {}
    url = data.get('url', '')

    if not url:
        return {"ok": False, "error": "No URL provided"}

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'RDS-Encoder/1.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            # Read response and decode as UTF-8 (strict to preserve special chars like ä, ö, etc.)
            response_bytes = resp.read()
            try:
                response_text = response_bytes.decode('utf-8')
            except UnicodeDecodeError:
                # Fallback to latin-1 if not valid UTF-8
                response_text = response_bytes.decode('latin-1')
            json_data = json.loads(response_text)

        # Extract all field paths from JSON
        def extract_fields(obj, prefix=''):
            fields = []
            if isinstance(obj, dict):
                for key, value in obj.items():
                    current_path = f"{prefix}.{key}" if prefix else key
                    if isinstance(value, (dict, list)):
                        fields.extend(extract_fields(value, current_path))
                    else:
                        fields.append(current_path)
            elif isinstance(obj, list) and len(obj) > 0:
                # For arrays, use first element
                fields.extend(extract_fields(obj[0], f"{prefix}[0]"))
            return fields

        fields = extract_fields(json_data)

        # Extract sample values for each field
        def get_value_by_path(obj, path):
            parts = path.replace('[0]', '').split('.')
            current = obj
            for part in parts:
                if isinstance(current, list) and len(current) > 0:
                    current = current[0]
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return ''
            # Ensure proper UTF-8 encoding for string values
            if current is None:
                return ''
            elif isinstance(current, str):
                return current
            else:
                return str(current)

        sample = {field: get_value_by_path(json_data, field) for field in fields}

        return {"ok": True, "fields": fields, "sample": sample}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"Invalid JSON: {str(e)}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.route('/resolve-content', methods=['POST'])
def resolve_content():
    """Resolve content from file path or URL for preview."""
    if not session.get('auth'): return {"ok": False, "error": "unauthorized"}, 401
    data = request.get_json(silent=True) or {}
    source_type = data.get('source_type', 'manual')
    content = data.get('content', '')

    if not content:
        return {"ok": True, "resolved": ""}

    resolved = ""
    try:
        if source_type == 'file':
            with open(content, 'r', encoding='utf-8-sig', errors='replace') as f:
                resolved = f.read().strip()
        elif source_type == 'url':
            req = urllib.request.Request(content, headers={'User-Agent': 'RDS-Encoder/1.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                resolved = resp.read().decode('utf-8', errors='replace').strip()
        elif source_type == 'json':
            # Fetch JSON and extract fields
            url = content
            field1_path = data.get('json_field1', '')
            field2_path = data.get('json_field2', '')
            delimiter = data.get('split_delimiter', ' - ')
            hide_if_blank = data.get('json_hide_if_blank', False)
            
            req = urllib.request.Request(url, headers={'User-Agent': 'RDS-Encoder/1.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                response_bytes = resp.read()
                try:
                    response_text = response_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    response_text = response_bytes.decode('latin-1')
                json_data = json.loads(response_text)
            
            # Extract values by path
            def get_value_by_path(obj, path):
                if not path:
                    return ''
                parts = path.replace('[0]', '').split('.')
                current = obj
                for part in parts:
                    if isinstance(current, list) and len(current) > 0:
                        current = current[0]
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    else:
                        return ''
                if current is None:
                    return ''
                elif isinstance(current, str):
                    return current
                else:
                    return str(current)

            field1_value = get_value_by_path(json_data, field1_path)
            field2_value = get_value_by_path(json_data, field2_path) if field2_path else ''

            # Build message
            if hide_if_blank and not field1_value:
                # If Field 1 is blank and hide_if_blank is enabled, only show Field 2
                resolved = field2_value
            else:
                resolved = field1_value
                if field2_value:
                    resolved += delimiter + field2_value

        elif source_type == 'manual':
            # Check for inline dynamic patterns
            if "\\" in content:
                resolved = parse_text_source(content) or content
            else:
                resolved = content
    except Exception as e:
        print(f"[RDS] /resolve-content exception: {e}", flush=True)
        import traceback
        print(traceback.format_exc(), flush=True)
        return {"ok": False, "error": str(e), "resolved": ""}

    result = {"ok": True, "resolved": resolved}
    return result

@socketio.on('update')
def handle_update(data): 
    if not session.get('auth'): return
    Sanitize.to_state(data)

@socketio.on('control')
def handle_control(data):
    if not session.get('auth'): return
    if data['action'] == 'start':
        state["device_out_idx"] = int(data["dev_out"])
        state["device_in_idx"] = int(data["dev_in"])
        state["running"] = True
        save_config()
        threading.Thread(target=run_audio, daemon=True).start()
    else:
        state["running"] = False
        save_config()

# Dataset API Routes
@app.route('/datasets', methods=['GET'])
def get_datasets():
    if not session.get('auth'): return jsonify({'error': 'Not authenticated'}), 401
    return jsonify({'datasets': datasets, 'current': current_dataset})

@app.route('/datasets/<int:num>', methods=['PUT'])
def update_dataset(num):
    if not session.get('auth'): return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    datasets[str(num)] = {'name': data.get('name', f'Dataset {num}'), 'state': data.get('state', dict(state))}
    save_datasets()
    return jsonify({'success': True})

@app.route('/datasets/<int:num>/switch', methods=['POST'])
def switch_to_dataset(num):
    if not session.get('auth'): return jsonify({'error': 'Not authenticated'}), 401
    if switch_dataset(num):
        socketio.emit('dataset_changed', {'current': current_dataset})
        return jsonify({'success': True, 'current': current_dataset})
    return jsonify({'error': 'Dataset not found'}), 404

@app.route('/datasets/<int:num>', methods=['DELETE'])
def delete_dataset(num):
    global current_dataset
    if not session.get('auth'): return jsonify({'error': 'Not authenticated'}), 401
    num_str = str(num)
    if num_str in datasets and len(datasets) > 1:
        # If deleting the active dataset, switch to another one first
        # so current_dataset never points to a non-existent slot
        if current_dataset == int(num):
            other_key = next(k for k in sorted(datasets.keys(), key=int) if k != num_str)
            current_dataset = int(other_key)
            state.clear()
            state.update(default_state.copy())
            new_state = datasets[other_key]['state'].copy()
            new_state.pop('auto_start', None)
            state.update(new_state)
        del datasets[num_str]
        save_datasets()
        return jsonify({'success': True, 'current_dataset': current_dataset})
    return jsonify({'error': 'Cannot delete last dataset'}), 400

# Dynamic Control API Routes
@app.route('/dynamic_control/fetch_json', methods=['POST'])
def fetch_json_for_dynamic_control():
    """Proxy endpoint to fetch JSON URLs and avoid CORS issues"""
    if not session.get('auth'): 
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        data = request.json
        url = data.get('url', '')
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        import urllib.request
        import urllib.error
        
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'RDS-Encoder-Dynamic-Control/1.0')
        
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode('utf-8')
                json_data = json.loads(content)
                return jsonify({'success': True, 'data': json_data})
        except urllib.error.HTTPError as e:
            return jsonify({'error': f'HTTP {e.code}: {e.reason}'}), 400
        except urllib.error.URLError as e:
            return jsonify({'error': f'Connection error: {str(e.reason)}'}), 400
        except json.JSONDecodeError as e:
            return jsonify({'error': f'Invalid JSON: {str(e)}'}), 400
            
    except Exception as e:
        return jsonify({'error': f'Failed to fetch: {str(e)}'}), 500

# Custom Groups API Routes
@app.route('/custom_groups/export', methods=['GET'])
def export_custom_groups():
    if not session.get('auth'): return jsonify({'error': 'Not authenticated'}), 401
    try:
        custom_groups = json.loads(state.get("custom_groups", "[]"))
        return jsonify({'custom_groups': custom_groups, 'count': len(custom_groups)})
    except:
        return jsonify({'error': 'Failed to export custom groups'}), 500

# RDS2 Logo Upload Routes
@app.route('/rds2/upload_logo', methods=['POST'])
def upload_rds2_logo():
    if not session.get('auth'): return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    if 'logo' not in request.files:
        return jsonify({"ok": False, "error": "No file provided"}), 400
    
    file = request.files['logo']
    
    if file.filename == '':
        return jsonify({"ok": False, "error": "No file selected"}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        # Add timestamp to prevent overwrites
        timestamp = int(time.time())
        name, ext = os.path.splitext(filename)
        filename = f"rds2_logo_{timestamp}{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        
        file.save(filepath)
        state['rds2_logo_path'] = filepath
        state['rds2_logo_filename'] = filename  # Store just filename for display
        state['rds2_logo_reload'] = True  # Signal to audio thread to reload
        save_config()
        
        return jsonify({
            "ok": True,
            "path": filepath,
            "filename": filename,
            "url": f"/uploads/{filename}"
        })
    
    return jsonify({"ok": False, "error": "Invalid file type. Use PNG, JPG, GIF, or BMP"}), 400

@app.route('/rds2/clear_logo', methods=['POST'])
def clear_rds2_logo():
    if not session.get('auth'): return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    state['rds2_logo_path'] = ""
    state['rds2_logo_filename'] = ""
    state['rds2_logo_reload'] = False
    save_config()
    return jsonify({"ok": True})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    try:
        # Secure the filename to prevent directory traversal
        safe_name = secure_filename(filename)
        filepath = os.path.join(UPLOAD_FOLDER, safe_name)
        
        # Check if file exists
        if not os.path.exists(filepath):
            print(f"[UPLOADS] File not found: {filepath}", flush=True)
            return "File not found", 404
        
        return send_from_directory(UPLOAD_FOLDER, safe_name)
    except Exception as e:
        print(f"[UPLOADS] Error serving file {filename}: {e}", flush=True)
        return f"Error: {str(e)}", 500

@app.route('/custom_groups/import', methods=['POST'])
def import_custom_groups():
    if not session.get('auth'): return jsonify({'error': 'Not authenticated'}), 401
    try:
        data = request.json
        source_type = data.get('type', 'json')  # 'json', 'url', 'text'
        print(f"[IMPORT] Received request: type={source_type}, mode={data.get('mode')}", flush=True)

        if source_type == 'json':
            # Direct JSON data
            custom_groups = data.get('custom_groups', [])
            print(f"[IMPORT] JSON mode: received {len(custom_groups)} groups", flush=True)
        elif source_type == 'url':
            # Fetch from URL
            url = data.get('url', '')
            if not url:
                return jsonify({'error': 'URL required'}), 400

            import urllib.request
            try:
                with urllib.request.urlopen(url, timeout=10) as response:
                    content = response.read().decode('utf-8')
                    # Try to parse as JSON first
                    try:
                        custom_groups = json.loads(content)
                    except:
                        # Fall back to text format
                        default_group_type = data.get('default_group_type', 8)
                        default_version = data.get('default_version', 0)
                        custom_groups = parse_custom_groups_text(content, default_group_type, default_version)
            except Exception as e:
                return jsonify({'error': f'Failed to fetch URL: {str(e)}'}), 400
        elif source_type == 'text':
            # Parse text format (one group per line)
            text = data.get('text', '')
            default_group_type = data.get('default_group_type', 8)
            default_version = data.get('default_version', 0)
            custom_groups = parse_custom_groups_text(text, default_group_type, default_version)
        elif source_type == 'rdsspy':
            # Parse RDS Spy format
            text = data.get('text', '')
            custom_groups = parse_rds_spy_format(text)
        else:
            return jsonify({'error': 'Invalid source type'}), 400

        # Validate structure
        if not isinstance(custom_groups, list):
            return jsonify({'error': 'Invalid format: must be array'}), 400

        # Merge or replace
        mode = data.get('mode', 'replace')  # 'replace' or 'merge'
        print(f"[IMPORT] Mode: {mode}, Groups to process: {len(custom_groups)}", flush=True)
        if mode == 'merge':
            existing = json.loads(state.get("custom_groups", "[]"))
            print(f"[IMPORT] Merging: existing={len(existing)}, new={len(custom_groups)}", flush=True)
            existing.extend(custom_groups)
            custom_groups = existing

        print(f"[IMPORT] Saving {len(custom_groups)} total groups to state", flush=True)
        state["custom_groups"] = json.dumps(custom_groups)
        save_config()
        # Emit socket update to refresh all connected clients
        socketio.emit('state_update', {'custom_groups': state["custom_groups"]})
        print(f"[IMPORT] Success! Returning count={len(custom_groups)}", flush=True)
        return jsonify({'success': True, 'count': len(custom_groups)})
    except Exception as e:
        return jsonify({'error': f'Import failed: {str(e)}'}), 500

def parse_custom_groups_text(text, default_type=8, default_version=0):
    """Parse text format for custom groups.

    Supports two formats:
    Format 1: TYPE VERSION B2 B3 B4 [ENABLED]
        Example: 8 0 1F CAFE BEEF 1

    Format 2: B2 B3 B4
        Example: E0 594C 6201
        (Uses default_type and default_version for all entries)
    """
    groups = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        parts = line.split()
        if len(parts) == 3:
            # Format 2: B2 B3 B4
            try:
                group = {
                    'type': default_type,
                    'version': default_version,
                    'b2_tail': parts[0].upper(),
                    'b3': parts[1].upper(),
                    'b4': parts[2].upper(),
                    'enabled': True
                }
                groups.append(group)
            except:
                pass  # Skip invalid lines
        elif len(parts) >= 5:
            # Format 1: TYPE VERSION B2 B3 B4 [ENABLED]
            try:
                group = {
                    'type': int(parts[0]),
                    'version': int(parts[1]),
                    'b2_tail': parts[2].upper(),
                    'b3': parts[3].upper(),
                    'b4': parts[4].upper(),
                    'enabled': parts[5] == '1' if len(parts) > 5 else True
                }
                groups.append(group)
            except:
                pass  # Skip invalid lines
    return groups

def parse_rds_spy_format(text):
    """Parse RDS Spy log format.

    Format: PI B2 B3 B4 @timestamp
    Example: 5158 052F 8749 4920 @2024/12/31 20:18:18.16

    Where:
    - PI: Program Identification (ignored, we extract from data)
    - B2: Contains group type (bits 15-12), version (bit 11), and B2 tail (bits 4-0)
    - B3: Block 3 data (16 bits)
    - B4: Block 4 data (16 bits)
    - @timestamp: Timestamp (ignored)
    """
    groups = []
    for line in text.strip().split('\n'):
        line = line.strip()

        # Skip header lines
        if not line or line.startswith('<') or line.startswith('#'):
            continue

        # Split and get first 4 columns (ignore timestamp after @)
        parts = line.split()
        if len(parts) < 4:
            continue

        try:
            # Parse hex values
            pi_code = parts[0]  # PI code (not used, just for reference)
            b2_full = int(parts[1], 16)  # Full Block 2
            b3_val = parts[2].upper()    # Block 3 (as hex string)
            b4_val = parts[3].upper()    # Block 4 (as hex string)

            # Extract group type, version, and B2 tail from Block 2
            # Bits 15-12: Group type (0-15)
            # Bit 11: Version (0=A, 1=B)
            # Bits 10-5: Group-specific data
            # Bits 4-0: B2 tail (5 bits)
            group_type = (b2_full >> 12) & 0x0F  # Upper 4 bits
            version = (b2_full >> 11) & 0x01      # Bit 11
            b2_tail = b2_full & 0x1F              # Lower 5 bits

            group = {
                'type': group_type,
                'version': version,
                'b2_tail': f'{b2_tail:02X}',
                'b3': b3_val,
                'b4': b4_val,
                'enabled': True
            }
            groups.append(group)
        except:
            pass  # Skip invalid lines

    return groups

# Load configuration and datasets - order matters!
load_datasets()  # Load datasets first to populate the datasets dict
load_config()    # Then load config which applies the current dataset's state

# --- UECP SERVER ---
_uecp_tcp_server = None
_uecp_ws_client  = None

def _start_uecp_server():
    global _uecp_tcp_server, _uecp_ws_client
    tcp_enabled = state.get("uecp_enabled", False)
    ws_enabled  = state.get("uecp_ws_enabled", False)
    if not tcp_enabled and not ws_enabled:
        return
    try:
        from uecp_server import UECPStateHandler, UECPTCPServer, UECPWebSocketClient

        def _uecp_save():
            socketio.emit('state_update', {
                'custom_oda_list': state.get('custom_oda_list', '[]'),
                'custom_groups':   state.get('custom_groups',   '[]'),
            })
        def _uecp_restore():
            save_config()
            socketio.emit('state_update', {
                'custom_oda_list': state.get('custom_oda_list', '[]'),
                'custom_groups':   state.get('custom_groups',   '[]'),
            })

        handler = UECPStateHandler(state, _uecp_save, _uecp_restore)

        if tcp_enabled:
            srv = UECPTCPServer(
                str(state.get("uecp_host", "0.0.0.0")),
                int(state.get("uecp_port", 4001)),
                handler,
            )
            srv.start()
            _uecp_tcp_server = srv
            print(f"[UECP] TCP server listening on {srv.host}:{srv.port}")

        if ws_enabled:
            wsc = UECPWebSocketClient(
                str(state.get("uecp_ws_url", "ws://127.0.0.1/pacific")),
                handler,
            )
            wsc.start()
            _uecp_ws_client = wsc
            print(f"[UECP] WS client connecting to {wsc.url}")

    except Exception as e:
        print(f"[UECP] Failed to start: {e}")

_start_uecp_server()

def auto_start_if_enabled():
    if auto_start and not state.get("running"):
        # Ensure device indices are set from state
        if "device_out_idx" not in state or state["device_out_idx"] is None:
            print("Auto-start: No output device configured, skipping auto-start")
            return
        state["running"] = True
        print(f"Auto-start: Starting RDS encoder (out={state['device_out_idx']}, in={state.get('device_in_idx', -1)})")
        threading.Thread(target=run_audio, daemon=True).start()

# Delay auto-start slightly to ensure Flask/SocketIO is ready
def delayed_auto_start():
    import time
    time.sleep(0.5)  # Wait for server to initialize
    auto_start_if_enabled()

threading.Thread(target=delayed_auto_start, daemon=True).start()

LOGIN_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>RDS Master - Login</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background: #0f0f10; color: #e5e7eb; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .glass { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); box-shadow: 0 10px 40px rgba(0,0,0,0.35); }
    </style>
</head>
<body class="min-h-screen flex items-center justify-center px-4">
    <div class="glass rounded-md p-8 w-full max-w-sm">
        <div class="text-center mb-6">
            <div class="flex justify-center mb-2">
                <svg width="280" height="36" viewBox="0 0 2782 353" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path fill-rule="evenodd" clip-rule="evenodd" d="M280.133 50.036C258.107 72.0443 236.513 101.579 230.994 132.323C221.001 188.039 206.309 231.799 163.932 221.432C64.7423 143.095 212.838 84.1579 212.838 84.1579C212.838 84.1579 248.131 36.581 265.739 25.6349C105.794 -25.3699 -39.1536 97.0913 33.0134 215.802C71.703 279.458 141.541 299.148 210.866 289.651C245.504 284.898 273.786 248.392 292.994 222.732C300.178 213.144 307.188 204.061 313.304 193.926C319.958 193.454 340.326 41.7312 429.397 143.31C454.597 217.226 362.907 226.831 362.907 226.831C362.907 226.831 348.869 258.692 341.909 273.786C355.167 300.862 388.943 320.303 417.888 320.535C434.485 320.668 452.326 318.026 466.587 308.703C474.766 303.561 482.514 297.807 489.814 291.431C497.264 285.113 497.148 284.699 489.458 290.164C519.513 267.179 552.883 248.822 552.062 209.997C546.916 -32.7557 345.803 -15.5913 280.133 50.036Z" fill="white"/>
                    <path fill-rule="evenodd" clip-rule="evenodd" d="M348.654 176.447C348.654 156.053 365.086 139.526 385.355 139.526C405.64 139.526 422.072 156.053 422.072 176.447C422.072 196.832 405.64 213.359 385.355 213.359C365.086 213.359 348.654 196.832 348.654 176.447ZM213.949 214.444C223.412 204.657 229.287 191.599 229.287 176.811C229.287 89.9622 299.059 19.7975 385.421 19.7975C471.493 19.7975 541.091 89.9374 541.091 176.496C541.091 178.302 540.95 180.222 540.95 182.028L523.698 168.258C519.48 95.0875 459.146 36.9288 385.355 36.9288C308.498 36.9288 246.382 99.1861 246.382 176.48C246.382 205.303 229.453 230.673 204.867 241.61C195.917 245.593 185.692 248.127 175.218 248.127C140.174 248.127 111.006 222.657 105.131 189.09L121.124 176.488C121.124 206.586 145.295 230.888 175.218 230.888C183.902 230.888 192.222 228.826 199.505 225.224C204.867 222.533 209.731 218.816 213.949 214.444ZM86.5441 187.815C92.3032 231.931 129.8 266.268 175.201 266.268C188.112 266.268 200.616 263.37 211.753 258.493C242.918 244.806 264.455 212.92 264.455 176.496C264.455 109.238 318.483 55.1034 385.346 55.1034C448.655 55.1034 500.413 104.22 505.401 166.602L490.179 176.496C490.179 118.114 443.277 70.9265 385.346 70.9265C327.175 70.9265 280.216 117.982 280.216 176.488C280.216 218.931 254.975 255.504 219.128 272.412C205.977 278.622 190.664 282.083 175.201 282.083C117.395 282.083 70.2031 235.144 70.2031 177.142V176.496L86.5441 187.815ZM218.805 80.5478C205.513 74.2468 190.805 70.9016 175.201 70.9016C117.138 70.9016 70.2031 118.106 70.2031 176.488L54.8565 166.453C59.9776 104.08 111.884 55.0868 175.201 55.0868C193.871 55.0868 211.513 59.3262 227.249 66.919C230.828 61.9013 234.532 57.0161 238.377 52.3876C219.451 42.4847 197.956 36.9619 175.201 36.9619C101.277 36.9619 40.7861 95.0875 36.6926 168.523L19.4235 182.152C19.4235 180.222 19.2992 178.426 19.2992 176.488C19.2992 89.9291 89.1212 19.7064 175.193 19.7064C202.431 19.7064 228.127 26.7775 250.384 39.2638C255.232 34.4945 260.361 29.8825 265.731 25.6349C239.264 9.43092 208.314 0.29808 175.201 0.29808C78.3902 0.29808 0 79.132 0 176.496C0 273.861 78.3902 352.687 175.201 352.687C208.314 352.687 239.421 343.537 265.764 327.457C305.829 303.015 335.329 262.418 345.779 214.99C355.697 225.166 369.561 231.368 384.849 231.368C415.012 231.368 439.457 206.768 439.457 176.447L455.003 188.329C449.368 222.028 420.216 247.754 385.172 247.754C374.682 247.754 364.837 245.444 355.88 241.445C353.841 247.373 351.529 253.153 348.853 258.684C359.973 263.569 372.254 266.268 385.172 266.268C430.442 266.268 467.797 232.312 473.672 188.461L490.171 176.886C490.171 235.028 442.979 282.091 385.172 282.091C369.569 282.091 354.562 278.572 341.254 272.536C329.114 293.783 313.329 312.239 294.344 327.391C320.828 343.479 352.06 352.703 385.172 352.703C481.851 352.703 560.249 273.869 560.249 176.513C560.249 79.1568 482.066 0 385.38 0C320.256 0 263.61 35.4632 233.265 88.3973C228.674 85.2757 223.793 82.858 218.805 80.5478ZM138.509 176.513C138.509 156.128 154.941 139.593 175.226 139.593C195.495 139.593 211.927 156.128 211.927 176.513C211.927 196.898 195.495 213.434 175.226 213.434C154.941 213.434 138.509 196.898 138.509 176.513Z" fill="#E52976"/>
                    <path d="M663.572 299.932L698.362 51.4321H793.679C807.169 51.4321 818.884 54.8046 828.824 61.5496C838.764 68.2946 846.101 77.3471 850.834 88.7071C855.686 99.9488 857.165 112.433 855.272 126.16C853.733 137.401 850.243 147.755 844.799 157.222C839.356 166.57 832.493 174.676 824.209 181.54C816.044 188.403 806.933 193.61 796.874 197.16L842.137 299.932H792.259L748.594 201.065H720.727L706.882 299.932H663.572ZM726.762 157.755H773.799C779.953 157.755 785.751 156.216 791.194 153.14C796.756 150.063 801.43 145.921 805.217 140.715C809.122 135.508 811.488 129.71 812.317 123.32C813.263 116.811 812.553 110.954 810.187 105.747C807.938 100.54 804.448 96.3988 799.714 93.3221C795.099 90.2455 789.715 88.7071 783.562 88.7071H736.524L726.762 157.755Z" fill="white"/>
                    <path d="M876.086 299.932L910.876 51.4321H993.236C1010.39 51.4321 1026.01 54.6863 1040.1 61.1946C1054.18 67.5846 1066.13 76.5188 1075.95 87.9971C1085.77 99.3571 1092.81 112.551 1097.07 127.58C1101.45 142.49 1102.46 158.524 1100.09 175.682C1097.72 192.84 1092.22 208.934 1083.58 223.962C1075.06 238.872 1064.3 252.066 1051.28 263.545C1038.38 274.905 1023.94 283.839 1007.97 290.347C992.112 296.737 975.605 299.932 958.446 299.932H876.086ZM925.431 256.622H964.481C975.486 256.622 986.136 254.551 996.431 250.41C1006.84 246.15 1016.31 240.351 1024.83 233.015C1033.35 225.56 1040.39 216.98 1045.95 207.277C1051.52 197.455 1055.12 186.924 1056.78 175.682C1058.32 164.44 1057.61 153.968 1054.65 144.265C1051.81 134.561 1047.2 125.982 1040.81 118.527C1034.42 111.072 1026.61 105.274 1017.38 101.132C1008.26 96.8721 998.147 94.7421 987.024 94.7421H948.151L925.431 256.622Z" fill="white"/>
                    <path d="M1204.39 304.547C1190.67 304.547 1177.77 302.417 1165.7 298.157C1153.75 293.779 1143.63 287.566 1135.34 279.52C1127.06 271.355 1121.62 261.592 1119.01 250.232L1161.79 235.322C1162.86 240.055 1165.64 244.375 1170.13 248.28C1174.63 252.185 1180.37 255.32 1187.35 257.687C1194.33 260.054 1202.03 261.237 1210.43 261.237C1219.07 261.237 1227.29 259.935 1235.1 257.332C1243.03 254.61 1249.65 250.883 1254.98 246.15C1260.3 241.298 1263.44 235.677 1264.39 229.287C1265.33 222.779 1263.62 217.454 1259.24 213.312C1254.98 209.052 1249.24 205.68 1242.02 203.195C1234.92 200.71 1227.41 198.816 1219.48 197.515C1202.2 194.793 1187 190.592 1173.86 184.912C1160.73 179.232 1150.79 171.363 1144.04 161.305C1137.42 151.246 1135.17 138.23 1137.3 122.255C1139.43 107.226 1145.58 94.0913 1155.76 82.8496C1166.05 71.6079 1178.65 62.8513 1193.56 56.5796C1208.59 50.308 1224.27 47.1721 1240.6 47.1721C1254.21 47.1721 1266.99 49.3021 1278.94 53.5621C1291.01 57.8221 1301.25 64.0346 1309.65 72.1996C1318.05 80.3646 1323.55 90.1863 1326.16 101.665L1283.2 116.397C1282.14 111.664 1279.36 107.345 1274.86 103.44C1270.36 99.5346 1264.62 96.458 1257.64 94.2096C1250.66 91.843 1242.97 90.6596 1234.57 90.6596C1226.17 90.5413 1218 91.9021 1210.07 94.7421C1202.14 97.4638 1195.46 101.191 1190.01 105.925C1184.69 110.658 1181.55 116.101 1180.61 122.255C1179.54 130.183 1180.96 136.159 1184.87 140.182C1188.77 144.205 1194.27 147.164 1201.37 149.057C1208.59 150.832 1216.64 152.489 1225.51 154.027C1241.84 156.63 1256.58 161.009 1269.71 167.162C1282.97 173.315 1293.14 181.54 1300.24 191.835C1307.34 202.011 1309.83 214.495 1307.7 229.287C1305.57 244.315 1299.41 257.51 1289.24 268.87C1279.18 280.111 1266.64 288.868 1251.61 295.14C1236.7 301.411 1220.96 304.547 1204.39 304.547Z" fill="white"/>
                    <path d="M1418.55 299.932L1466.83 51.4321H1502.68L1563.21 210.65L1623.74 51.4321H1659.59L1707.87 299.932H1663.68L1633.86 146.217L1577.94 294.252H1548.3L1492.39 146.217L1462.75 299.932H1418.55Z" fill="#E52976"/>
                    <path d="M1814.67 51.4321H1864.55L1954.9 299.932H1908.92L1892.95 255.912H1786.45L1770.47 299.932H1724.5L1814.67 51.4321ZM1802.25 212.602H1876.97L1839.52 110.185L1802.25 212.602Z" fill="#E52976"/>
                    <path d="M2059.23 304.547C2045.5 304.547 2032.31 302.417 2019.65 298.157C2007.1 293.779 1996.1 287.566 1986.63 279.52C1977.17 271.355 1970.36 261.592 1966.22 250.232L2006.87 235.322C2008.52 240.055 2011.9 244.375 2016.98 248.28C2022.07 252.185 2028.29 255.32 2035.62 257.687C2042.96 260.054 2050.83 261.237 2059.23 261.237C2067.87 261.237 2075.91 259.935 2083.37 257.332C2090.94 254.61 2097.04 250.883 2101.65 246.15C2106.27 241.298 2108.57 235.677 2108.57 229.287C2108.57 222.779 2106.15 217.454 2101.3 213.312C2096.44 209.052 2090.23 205.68 2082.66 203.195C2075.09 200.71 2067.28 198.816 2059.23 197.515C2041.6 194.793 2025.8 190.592 2011.84 184.912C1997.87 179.232 1986.81 171.363 1978.64 161.305C1970.6 151.246 1966.57 138.23 1966.57 122.255C1966.57 107.226 1970.89 94.0913 1979.53 82.8496C1988.17 71.6079 1999.53 62.8513 2013.61 56.5796C2027.69 50.308 2042.9 47.1721 2059.23 47.1721C2072.84 47.1721 2085.97 49.3021 2098.63 53.5621C2111.3 57.8221 2122.36 64.0346 2131.83 72.1996C2141.41 80.3646 2148.28 90.1863 2152.42 101.665L2111.59 116.397C2109.94 111.664 2106.56 107.345 2101.47 103.44C2096.39 99.5346 2090.17 96.458 2082.84 94.2096C2075.5 91.843 2067.63 90.6596 2059.23 90.6596C2050.83 90.5413 2042.84 91.9021 2035.27 94.7421C2027.81 97.4638 2021.72 101.191 2016.98 105.925C2012.25 110.658 2009.88 116.101 2009.88 122.255C2009.88 130.183 2012.07 136.159 2016.45 140.182C2020.95 144.205 2026.92 147.164 2034.38 149.057C2041.83 150.832 2050.12 152.489 2059.23 154.027C2075.8 156.63 2091.12 161.009 2105.2 167.162C2119.28 173.315 2130.58 181.54 2139.1 191.835C2147.62 202.011 2151.88 214.495 2151.88 229.287C2151.88 244.315 2147.62 257.51 2139.1 268.87C2130.58 280.111 2119.28 288.868 2105.2 295.14C2091.12 301.411 2075.8 304.547 2059.23 304.547Z" fill="#E52976"/>
                    <path d="M2371.47 51.4321V94.7421H2295.5V299.932H2252.19V94.7421H2176.22V51.4321H2371.47Z" fill="#E52976"/>
                    <path d="M2406.93 299.932V51.4321H2562.78V94.7421H2450.24V145.152H2539.52V188.462H2450.24V256.622H2562.78V299.932H2406.93Z" fill="#E52976"/>
                    <path d="M2602.81 299.932V51.4321H2698.12C2711.61 51.4321 2723.8 54.8046 2734.69 61.5496C2745.58 68.2946 2754.21 77.3471 2760.6 88.7071C2766.99 99.9488 2770.19 112.433 2770.19 126.16C2770.19 137.401 2768.12 147.755 2763.98 157.222C2759.95 166.57 2754.27 174.676 2746.94 181.54C2739.72 188.403 2731.38 193.61 2721.91 197.16L2781.37 299.932H2731.49L2673.98 201.065H2646.12V299.932H2602.81ZM2646.12 157.755H2693.15C2699.31 157.755 2704.93 156.216 2710.02 153.14C2715.1 150.063 2719.19 145.921 2722.26 140.715C2725.34 135.508 2726.88 129.71 2726.88 123.32C2726.88 116.811 2725.34 110.954 2722.26 105.747C2719.19 100.54 2715.1 96.3988 2710.02 93.3221C2704.93 90.2455 2699.31 88.7071 2693.15 88.7071H2646.12V157.755Z" fill="#E52976"/>
                </svg>
            </div>
            <div class="text-xs text-gray-400">{{version}} • {{site_name}}</div>
        </div>
        {% if msg %}
        <div class="mb-4 text-sm text-red-300 bg-red-900/40 border border-red-700 rounded px-3 py-2">{{ msg }}</div>
        {% endif %}
        <form method="POST" class="space-y-4">
            <div>
                <label class="block text-xs text-gray-400 mb-1">Username</label>
                <input name="user" value="{{ user }}" class="w-full bg-black/60 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-pink-500" autofocus>
            </div>
            <div>
                <label class="block text-xs text-gray-400 mb-1">Password</label>
                <input type="password" name="pass" class="w-full bg-black/60 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-pink-500">
            </div>
            <button type="submit" class="w-full bg-pink-600 hover:bg-pink-500 text-white font-semibold rounded py-2 transition">Sign In</button>
        </form>
        <div class="text-[11px] text-gray-500 mt-4">Credentials are loaded from config.ini [AUTH].</div>
    </div>
</body>
</html>
"""

UI_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>RDS Master - {{site_name}}</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background: #1a1a1a; color: #e0e0e0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; font-size: 12px; overflow: hidden; }
        .app-container { display: flex; height: 100vh; flex-direction: column; }
        .header { background: #2d1b4e; border-bottom: 2px solid #d946ef; padding: 10px 15px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3); }
        .logo { font-size: 18px; font-weight: bold; color: #fff; letter-spacing: 1px; }
        .logo span { color: #d946ef; font-style: italic; }
        .workspace { display: flex; flex: 1; overflow: hidden; }
        .sidebar { width: 180px; background: #222; border-right: 1px solid #333; display: flex; flex-direction: column; }
        .tab-btn { padding: 12px 15px; cursor: pointer; color: #aaa; border-left: 3px solid transparent; transition: all 0.2s; text-align: left; font-weight: 600; font-size: 11px; text-transform: uppercase; }
        .tab-btn:hover { background: #2a2a2a; color: #fff; }
        .tab-btn.active { background: #2d1b4e; color: #d946ef; border-left-color: #d946ef; }
        .content { flex: 1; padding: 20px; overflow-y: auto; background: #1a1a1a; display: none; }
        .content.active { display: block; animation: fadeIn 0.3s; }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        .section { background: #252525; border: 1px solid #333; border-radius: 4px; margin-bottom: 15px; overflow: hidden; }
        .section-header { background: #333; color: #d946ef; padding: 6px 10px; font-weight: bold; font-size: 11px; text-transform: uppercase; border-bottom: 1px solid #444; display: flex; align-items: center; gap: 5px; }
        .section-body { padding: 10px; display: grid; gap: 10px; }
        input[type="text"], input[type="number"], select, textarea {
            background: #111; border: 1px solid #444; color: #f0f0f0; padding: 4px 8px; font-size: 12px; width: 100%; border-radius: 2px;
        }
        input:focus, select:focus, textarea:focus { border-color: #d946ef; outline: none; }
        label { color: #999; font-size: 10px; text-transform: uppercase; display: block; margin-bottom: 2px; font-weight: 600; }
        .toggle-checkbox { appearance: none; width: 30px; height: 16px; background: #444; border-radius: 10px; position: relative; cursor: pointer; outline: none; }
        .toggle-checkbox:checked { background: #d946ef; }
        .toggle-checkbox::after { content: ''; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px; background: #fff; border-radius: 50%; transition: 0.2s; }
        .toggle-checkbox:checked::after { left: 16px; }
        .pwr-btn {
            background: #1f2937; border: 1px solid #374151; color: #9ca3af; padding: 5px 15px; border-radius: 4px; font-weight: bold; cursor: pointer; transition: 0.3s;
        }
        .pwr-btn.on { background: #d946ef; color: white; border-color: #c026d3; box-shadow: 0 0 10px rgba(217, 70, 239, 0.4); }
        .save-btn {
            background: #1f2937; border: 1px solid #374151; color: #9ca3af; padding: 5px 15px; border-radius: 4px; font-weight: bold; cursor: pointer; transition: 0.3s; display: none;
        }
        .save-btn.has-changes { background: #1e40af; color: white; border-color: #1e3a8a; display: inline-block; }
        .slider-container { display: flex; align-items: center; gap: 10px; }
        .slider-val { width: 30px; text-align: right; }
        input[type=range] { flex: 1; accent-color: #d946ef; }
        
        .live-display { background: #000; border: 1px solid #333; color: #0f0; font-family: 'Courier New', monospace; padding: 8px; font-size: 14px; font-weight: bold; border-radius: 2px; letter-spacing: 1px; min-height: 20px; }
        .live-display.lg { font-size: 24px; color: #d946ef; text-align: center; letter-spacing: 4px; background: #110011; border-color: #550055; }
        .live-display.sub { color: #00ffcc; font-size: 12px; }
        
        /* DI Flag checkboxes - red/green indicators */
        input[type="checkbox"].di-indicator { 
            appearance: none; 
            width: 16px; 
            height: 16px; 
            border-radius: 3px; 
            border: 2px solid #666;
            background: #dc2626;
            position: relative;
            cursor: default;
        }
        input[type="checkbox"].di-indicator:checked { 
            background: #10b981;
            border-color: #059669;
        }
        input[type="checkbox"].di-indicator:checked::after {
            content: '✓';
            position: absolute;
            color: white;
            font-size: 12px;
            font-weight: bold;
            top: -2px;
            left: 2px;
        }

        /* RT+ Builder Modal */
        .rtplus-modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.8); z-index: 100; display: flex; align-items: center; justify-content: center; padding: 20px; }
        .rtplus-modal { background: #1a1a1a; border: 1px solid #444; border-radius: 8px; max-width: 700px; width: 100%; max-height: 90vh; overflow: hidden; display: flex; flex-direction: column; }
        .rtplus-modal-content { background: #1a1a1a; border: 1px solid #444; border-radius: 8px; width: 100%; max-height: 90vh; overflow-y: auto; padding: 20px; }
        .rtplus-modal-header { background: #2d1b4e; border-bottom: 2px solid #d946ef; padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; }
        .rtplus-modal-body { padding: 16px; overflow-y: auto; flex: 1; }
        .rtplus-modal-footer { background: #252525; border-top: 1px solid #333; padding: 12px 16px; display: flex; justify-content: space-between; }
        .rtplus-tag-1 { background: rgba(251, 146, 60, 0.25); border-bottom: 2px solid #f97316; color: #fdba74; padding: 0 2px; }
        .rtplus-tag-2 { background: rgba(34, 211, 238, 0.25); border-bottom: 2px solid #06b6d4; color: #67e8f9; padding: 0 2px; }
        .rtplus-preview { font-family: 'Courier New', monospace; font-size: 16px; letter-spacing: 1px; background: #0a0a0a; border: 1px solid #333; padding: 12px; border-radius: 4px; min-height: 40px; }

        /* RT Message Cards */
        .rt-msg-card { background: #252525; border: 1px solid #333; border-radius: 4px; overflow: hidden; cursor: move; transition: all 0.2s; }
        .rt-msg-card.disabled { opacity: 0.5; }
        .rt-msg-card.dragging { opacity: 0.5; transform: scale(0.95); }
        .rt-msg-card.drag-over { border-color: #7c3aed; border-width: 2px; margin-top: 4px; }
        .rt-msg-header { display: flex; align-items: center; gap: 8px; padding: 8px 12px; cursor: pointer; }
        .rt-msg-header:hover { background: #2a2a2a; }
        .rt-msg-buffer { padding: 2px 8px; border-radius: 3px; font-size: 10px; font-weight: bold; }
        .buffer-a { background: #dc2626; color: white; }
        .buffer-b { background: #2563eb; color: white; }
        .buffer-ab { background: #7c3aed; color: white; }
        .rt-msg-duration { color: #888; font-size: 11px; min-width: 30px; }
        .rt-msg-source { font-size: 12px; }
        .rt-msg-preview { flex: 1; color: #ccc; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: monospace; }
        .rt-msg-rtplus { color: #f97316; font-size: 10px; font-weight: bold; }
        .rt-msg-actions { display: flex; gap: 4px; }
        .rt-msg-actions button { background: transparent; border: none; color: #666; cursor: pointer; padding: 2px 6px; font-size: 14px; }
        .rt-msg-actions button:hover { color: #fff; }

        /* RDS Spy Group Grid */
        .rdsspy-group-box {
            border-radius: 4px;
            padding: 8px 4px;
            text-align: center;
            transition: all 0.2s;
            border: 2px solid transparent;
        }
        .rdsspy-group-disabled {
            background: #1a1a1a;
            border-color: #333;
            color: #555;
            cursor: not-allowed;
        }
        .rdsspy-group-detected {
            cursor: pointer;
        }
        .rdsspy-group-enabled {
            background: linear-gradient(135deg, #7c3aed 0%, #a855f7 100%);
            border-color: #a855f7;
            color: white;
            box-shadow: 0 0 10px rgba(168, 85, 247, 0.5);
        }
        .rdsspy-group-enabled:hover {
            background: linear-gradient(135deg, #6d28d9 0%, #9333ea 100%);
            transform: scale(1.05);
        }
        .rdsspy-group-toggled-off {
            background: #2a2a2a;
            border-color: #555;
            color: #888;
            text-decoration: line-through;
        }
        .rdsspy-group-toggled-off:hover {
            border-color: #888;
        }
    </style>
</head>
<body>
    <div class="app-container">
        <div class="header">
            <svg width="180" height="23" viewBox="0 0 2782 353" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path fill-rule="evenodd" clip-rule="evenodd" d="M280.133 50.036C258.107 72.0443 236.513 101.579 230.994 132.323C221.001 188.039 206.309 231.799 163.932 221.432C64.7423 143.095 212.838 84.1579 212.838 84.1579C212.838 84.1579 248.131 36.581 265.739 25.6349C105.794 -25.3699 -39.1536 97.0913 33.0134 215.802C71.703 279.458 141.541 299.148 210.866 289.651C245.504 284.898 273.786 248.392 292.994 222.732C300.178 213.144 307.188 204.061 313.304 193.926C319.958 193.454 340.326 41.7312 429.397 143.31C454.597 217.226 362.907 226.831 362.907 226.831C362.907 226.831 348.869 258.692 341.909 273.786C355.167 300.862 388.943 320.303 417.888 320.535C434.485 320.668 452.326 318.026 466.587 308.703C474.766 303.561 482.514 297.807 489.814 291.431C497.264 285.113 497.148 284.699 489.458 290.164C519.513 267.179 552.883 248.822 552.062 209.997C546.916 -32.7557 345.803 -15.5913 280.133 50.036Z" fill="white"/>
                <path fill-rule="evenodd" clip-rule="evenodd" d="M348.654 176.447C348.654 156.053 365.086 139.526 385.355 139.526C405.64 139.526 422.072 156.053 422.072 176.447C422.072 196.832 405.64 213.359 385.355 213.359C365.086 213.359 348.654 196.832 348.654 176.447ZM213.949 214.444C223.412 204.657 229.287 191.599 229.287 176.811C229.287 89.9622 299.059 19.7975 385.421 19.7975C471.493 19.7975 541.091 89.9374 541.091 176.496C541.091 178.302 540.95 180.222 540.95 182.028L523.698 168.258C519.48 95.0875 459.146 36.9288 385.355 36.9288C308.498 36.9288 246.382 99.1861 246.382 176.48C246.382 205.303 229.453 230.673 204.867 241.61C195.917 245.593 185.692 248.127 175.218 248.127C140.174 248.127 111.006 222.657 105.131 189.09L121.124 176.488C121.124 206.586 145.295 230.888 175.218 230.888C183.902 230.888 192.222 228.826 199.505 225.224C204.867 222.533 209.731 218.816 213.949 214.444ZM86.5441 187.815C92.3032 231.931 129.8 266.268 175.201 266.268C188.112 266.268 200.616 263.37 211.753 258.493C242.918 244.806 264.455 212.92 264.455 176.496C264.455 109.238 318.483 55.1034 385.346 55.1034C448.655 55.1034 500.413 104.22 505.401 166.602L490.179 176.496C490.179 118.114 443.277 70.9265 385.346 70.9265C327.175 70.9265 280.216 117.982 280.216 176.488C280.216 218.931 254.975 255.504 219.128 272.412C205.977 278.622 190.664 282.083 175.201 282.083C117.395 282.083 70.2031 235.144 70.2031 177.142V176.496L86.5441 187.815ZM218.805 80.5478C205.513 74.2468 190.805 70.9016 175.201 70.9016C117.138 70.9016 70.2031 118.106 70.2031 176.488L54.8565 166.453C59.9776 104.08 111.884 55.0868 175.201 55.0868C193.871 55.0868 211.513 59.3262 227.249 66.919C230.828 61.9013 234.532 57.0161 238.377 52.3876C219.451 42.4847 197.956 36.9619 175.201 36.9619C101.277 36.9619 40.7861 95.0875 36.6926 168.523L19.4235 182.152C19.4235 180.222 19.2992 178.426 19.2992 176.488C19.2992 89.9291 89.1212 19.7064 175.193 19.7064C202.431 19.7064 228.127 26.7775 250.384 39.2638C255.232 34.4945 260.361 29.8825 265.731 25.6349C239.264 9.43092 208.314 0.29808 175.201 0.29808C78.3902 0.29808 0 79.132 0 176.496C0 273.861 78.3902 352.687 175.201 352.687C208.314 352.687 239.421 343.537 265.764 327.457C305.829 303.015 335.329 262.418 345.779 214.99C355.697 225.166 369.561 231.368 384.849 231.368C415.012 231.368 439.457 206.768 439.457 176.447L455.003 188.329C449.368 222.028 420.216 247.754 385.172 247.754C374.682 247.754 364.837 245.444 355.88 241.445C353.841 247.373 351.529 253.153 348.853 258.684C359.973 263.569 372.254 266.268 385.172 266.268C430.442 266.268 467.797 232.312 473.672 188.461L490.171 176.886C490.171 235.028 442.979 282.091 385.172 282.091C369.569 282.091 354.562 278.572 341.254 272.536C329.114 293.783 313.329 312.239 294.344 327.391C320.828 343.479 352.06 352.703 385.172 352.703C481.851 352.703 560.249 273.869 560.249 176.513C560.249 79.1568 482.066 0 385.38 0C320.256 0 263.61 35.4632 233.265 88.3973C228.674 85.2757 223.793 82.858 218.805 80.5478ZM138.509 176.513C138.509 156.128 154.941 139.593 175.226 139.593C195.495 139.593 211.927 156.128 211.927 176.513C211.927 196.898 195.495 213.434 175.226 213.434C154.941 213.434 138.509 196.898 138.509 176.513Z" fill="#E52976"/>
                <path d="M663.572 299.932L698.362 51.4321H793.679C807.169 51.4321 818.884 54.8046 828.824 61.5496C838.764 68.2946 846.101 77.3471 850.834 88.7071C855.686 99.9488 857.165 112.433 855.272 126.16C853.733 137.401 850.243 147.755 844.799 157.222C839.356 166.57 832.493 174.676 824.209 181.54C816.044 188.403 806.933 193.61 796.874 197.16L842.137 299.932H792.259L748.594 201.065H720.727L706.882 299.932H663.572ZM726.762 157.755H773.799C779.953 157.755 785.751 156.216 791.194 153.14C796.756 150.063 801.43 145.921 805.217 140.715C809.122 135.508 811.488 129.71 812.317 123.32C813.263 116.811 812.553 110.954 810.187 105.747C807.938 100.54 804.448 96.3988 799.714 93.3221C795.099 90.2455 789.715 88.7071 783.562 88.7071H736.524L726.762 157.755Z" fill="white"/>
                <path d="M876.086 299.932L910.876 51.4321H993.236C1010.39 51.4321 1026.01 54.6863 1040.1 61.1946C1054.18 67.5846 1066.13 76.5188 1075.95 87.9971C1085.77 99.3571 1092.81 112.551 1097.07 127.58C1101.45 142.49 1102.46 158.524 1100.09 175.682C1097.72 192.84 1092.22 208.934 1083.58 223.962C1075.06 238.872 1064.3 252.066 1051.28 263.545C1038.38 274.905 1023.94 283.839 1007.97 290.347C992.112 296.737 975.605 299.932 958.446 299.932H876.086ZM925.431 256.622H964.481C975.486 256.622 986.136 254.551 996.431 250.41C1006.84 246.15 1016.31 240.351 1024.83 233.015C1033.35 225.56 1040.39 216.98 1045.95 207.277C1051.52 197.455 1055.12 186.924 1056.78 175.682C1058.32 164.44 1057.61 153.968 1054.65 144.265C1051.81 134.561 1047.2 125.982 1040.81 118.527C1034.42 111.072 1026.61 105.274 1017.38 101.132C1008.26 96.8721 998.147 94.7421 987.024 94.7421H948.151L925.431 256.622Z" fill="white"/>
                <path d="M1204.39 304.547C1190.67 304.547 1177.77 302.417 1165.7 298.157C1153.75 293.779 1143.63 287.566 1135.34 279.52C1127.06 271.355 1121.62 261.592 1119.01 250.232L1161.79 235.322C1162.86 240.055 1165.64 244.375 1170.13 248.28C1174.63 252.185 1180.37 255.32 1187.35 257.687C1194.33 260.054 1202.03 261.237 1210.43 261.237C1219.07 261.237 1227.29 259.935 1235.1 257.332C1243.03 254.61 1249.65 250.883 1254.98 246.15C1260.3 241.298 1263.44 235.677 1264.39 229.287C1265.33 222.779 1263.62 217.454 1259.24 213.312C1254.98 209.052 1249.24 205.68 1242.02 203.195C1234.92 200.71 1227.41 198.816 1219.48 197.515C1202.2 194.793 1187 190.592 1173.86 184.912C1160.73 179.232 1150.79 171.363 1144.04 161.305C1137.42 151.246 1135.17 138.23 1137.3 122.255C1139.43 107.226 1145.58 94.0913 1155.76 82.8496C1166.05 71.6079 1178.65 62.8513 1193.56 56.5796C1208.59 50.308 1224.27 47.1721 1240.6 47.1721C1254.21 47.1721 1266.99 49.3021 1278.94 53.5621C1291.01 57.8221 1301.25 64.0346 1309.65 72.1996C1318.05 80.3646 1323.55 90.1863 1326.16 101.665L1283.2 116.397C1282.14 111.664 1279.36 107.345 1274.86 103.44C1270.36 99.5346 1264.62 96.458 1257.64 94.2096C1250.66 91.843 1242.97 90.6596 1234.57 90.6596C1226.17 90.5413 1218 91.9021 1210.07 94.7421C1202.14 97.4638 1195.46 101.191 1190.01 105.925C1184.69 110.658 1181.55 116.101 1180.61 122.255C1179.54 130.183 1180.96 136.159 1184.87 140.182C1188.77 144.205 1194.27 147.164 1201.37 149.057C1208.59 150.832 1216.64 152.489 1225.51 154.027C1241.84 156.63 1256.58 161.009 1269.71 167.162C1282.97 173.315 1293.14 181.54 1300.24 191.835C1307.34 202.011 1309.83 214.495 1307.7 229.287C1305.57 244.315 1299.41 257.51 1289.24 268.87C1279.18 280.111 1266.64 288.868 1251.61 295.14C1236.7 301.411 1220.96 304.547 1204.39 304.547Z" fill="white"/>
                <path d="M1418.55 299.932L1466.83 51.4321H1502.68L1563.21 210.65L1623.74 51.4321H1659.59L1707.87 299.932H1663.68L1633.86 146.217L1577.94 294.252H1548.3L1492.39 146.217L1462.75 299.932H1418.55Z" fill="#E52976"/>
                <path d="M1814.67 51.4321H1864.55L1954.9 299.932H1908.92L1892.95 255.912H1786.45L1770.47 299.932H1724.5L1814.67 51.4321ZM1802.25 212.602H1876.97L1839.52 110.185L1802.25 212.602Z" fill="#E52976"/>
                <path d="M2059.23 304.547C2045.5 304.547 2032.31 302.417 2019.65 298.157C2007.1 293.779 1996.1 287.566 1986.63 279.52C1977.17 271.355 1970.36 261.592 1966.22 250.232L2006.87 235.322C2008.52 240.055 2011.9 244.375 2016.98 248.28C2022.07 252.185 2028.29 255.32 2035.62 257.687C2042.96 260.054 2050.83 261.237 2059.23 261.237C2067.87 261.237 2075.91 259.935 2083.37 257.332C2090.94 254.61 2097.04 250.883 2101.65 246.15C2106.27 241.298 2108.57 235.677 2108.57 229.287C2108.57 222.779 2106.15 217.454 2101.3 213.312C2096.44 209.052 2090.23 205.68 2082.66 203.195C2075.09 200.71 2067.28 198.816 2059.23 197.515C2041.6 194.793 2025.8 190.592 2011.84 184.912C1997.87 179.232 1986.81 171.363 1978.64 161.305C1970.6 151.246 1966.57 138.23 1966.57 122.255C1966.57 107.226 1970.89 94.0913 1979.53 82.8496C1988.17 71.6079 1999.53 62.8513 2013.61 56.5796C2027.69 50.308 2042.9 47.1721 2059.23 47.1721C2072.84 47.1721 2085.97 49.3021 2098.63 53.5621C2111.3 57.8221 2122.36 64.0346 2131.83 72.1996C2141.41 80.3646 2148.28 90.1863 2152.42 101.665L2111.59 116.397C2109.94 111.664 2106.56 107.345 2101.47 103.44C2096.39 99.5346 2090.17 96.458 2082.84 94.2096C2075.5 91.843 2067.63 90.6596 2059.23 90.6596C2050.83 90.5413 2042.84 91.9021 2035.27 94.7421C2027.81 97.4638 2021.72 101.191 2016.98 105.925C2012.25 110.658 2009.88 116.101 2009.88 122.255C2009.88 130.183 2012.07 136.159 2016.45 140.182C2020.95 144.205 2026.92 147.164 2034.38 149.057C2041.83 150.832 2050.12 152.489 2059.23 154.027C2075.8 156.63 2091.12 161.009 2105.2 167.162C2119.28 173.315 2130.58 181.54 2139.1 191.835C2147.62 202.011 2151.88 214.495 2151.88 229.287C2151.88 244.315 2147.62 257.51 2139.1 268.87C2130.58 280.111 2119.28 288.868 2105.2 295.14C2091.12 301.411 2075.8 304.547 2059.23 304.547Z" fill="#E52976"/>
                <path d="M2371.47 51.4321V94.7421H2295.5V299.932H2252.19V94.7421H2176.22V51.4321H2371.47Z" fill="#E52976"/>
                <path d="M2406.93 299.932V51.4321H2562.78V94.7421H2450.24V145.152H2539.52V188.462H2450.24V256.622H2562.78V299.932H2406.93Z" fill="#E52976"/>
                <path d="M2602.81 299.932V51.4321H2698.12C2711.61 51.4321 2723.8 54.8046 2734.69 61.5496C2745.58 68.2946 2754.21 77.3471 2760.6 88.7071C2766.99 99.9488 2770.19 112.433 2770.19 126.16C2770.19 137.401 2768.12 147.755 2763.98 157.222C2759.95 166.57 2754.27 174.676 2746.94 181.54C2739.72 188.403 2731.38 193.61 2721.91 197.16L2781.37 299.932H2731.49L2673.98 201.065H2646.12V299.932H2602.81ZM2646.12 157.755H2693.15C2699.31 157.755 2704.93 156.216 2710.02 153.14C2715.1 150.063 2719.19 145.921 2722.26 140.715C2725.34 135.508 2726.88 129.71 2726.88 123.32C2726.88 116.811 2725.34 110.954 2722.26 105.747C2719.19 100.54 2715.1 96.3988 2710.02 93.3221C2704.93 90.2455 2699.31 88.7071 2693.15 88.7071H2646.12V157.755Z" fill="#E52976"/>
            </svg>
            <div class="flex items-center gap-4">
                <div class="text-[10px] text-gray-400 flex items-center gap-2">
                    {{site_name}} • <span id="heartbeat" class="text-xs">♥</span> 192kHz Ready
                </div>
                <button id="pwrBtn" onclick="togglePower()" class="pwr-btn">OFF AIR</button>
                <a href="/logout" class="text-[11px] text-gray-300 hover:text-white underline">Logout</a>
            </div>
        </div>

        <div class="workspace">
            <div class="sidebar">
                <div class="tab-btn active" onclick="setTab('dashboard')">Dashboard</div>
                <div class="tab-btn" onclick="setTab('basic')">Basic RDS</div>
                <div class="tab-btn" onclick="setTab('expert')">Expert</div>
                <div class="tab-btn" onclick="setTab('audio')">Audio & MPX</div>
                <div class="tab-btn" onclick="setTab('rds2')">RDS2 Carriers</div>
                <div class="tab-btn" onclick="setTab('datasets')">Datasets</div>
                <div class="tab-btn" onclick="setTab('settings')">Settings</div>
                <div class="tab-btn" onclick="setTab('uecp')">UECP</div>
            </div>

            <div id="dashboard" class="content active">
                <div class="grid grid-cols-2 gap-4">
                    <div class="section col-span-2">
                        <div class="section-header">Live Output Monitor (WebSocket)</div>
                        <div class="section-body">
                             <div>
                                 <label>Program Service (PS)</label>
                                 <div class="live-display lg" id="live_ps">OFF AIR</div>
                             </div>
                             
                             <div class="grid grid-cols-4 gap-2">
                                 <div class="col-span-3">
                                     <label>RadioText (RT)</label>
                                     <div class="live-display sub" id="live_rt"></div>
                                 </div>
                                 <div>
                                     <label>PTY</label>
                                     <div class="live-display sub text-center" id="live_pty"></div>
                                 </div>
                             </div>
                             
                             <div>
                                 <label class="flex justify-between"><span>RT+ Tags</span> <span class="text-xs text-gray-400">AID: 4BD7 (Group 11A)</span></label>
                                 <div class="live-display sub text-orange-300 whitespace-pre-line" id="live_rt_plus"></div>
                             </div>

                             <div id="live_ert_row" style="display:none">
                                 <label class="flex justify-between"><span>Enhanced RadioText (eRT)</span> <span class="text-xs text-gray-400">ODA AID: 0458</span></label>
                                 <div class="live-display sub text-cyan-300" id="live_ert"></div>
                                 <div id="live_ert_rtplus_row" style="display:none" class="mt-1">
                                     <label class="text-xs text-gray-400">eRT RT+ Tags <span class="text-gray-500">(AID: 4BD8)</span></label>
                                     <div class="live-display sub text-orange-300 whitespace-pre-line" id="live_ert_rtplus"></div>
                                 </div>
                             </div>

                             <div class="grid grid-cols-3 gap-2">
                                 <div>
                                     <label>Live PI</label>
                                     <div class="live-display sub text-center text-yellow-300" id="live_pi"></div>
                                 </div>
                                 <div id="live_lps_col" class="col-span-2">
                                     <label>Long PS (Group 15)</label>
                                     <div class="live-display sub" id="live_lps"></div>
                                 </div>
                                 <div id="live_ptyn_col" style="display:none">
                                     <label>PTYN</label>
                                     <div class="live-display sub" id="live_ptyn"></div>
                                 </div>
                             </div>
                             <div id="live_pin_row" style="display:none">
                                 <label>Programme PIN</label>
                                 <div class="live-display sub text-center text-green-300" id="live_pin"></div>
                             </div>
                             
                             <div class="grid grid-cols-2 gap-2">
                                 <div>
                                     <label>Decoder Flags (DI)</label>
                                     <div class="live-display sub flex flex-wrap gap-x-3 gap-y-1 text-xs">
                                         <label class="flex items-center gap-1 cursor-default">
                                             <input type="checkbox" id="live_di_stereo" disabled class="pointer-events-none di-indicator">
                                             <span>Stereo</span>
                                         </label>
                                         <label class="flex items-center gap-1 cursor-default">
                                             <input type="checkbox" id="live_di_head" disabled class="pointer-events-none di-indicator">
                                             <span>Artificial Head</span>
                                         </label>
                                         <label class="flex items-center gap-1 cursor-default">
                                             <input type="checkbox" id="live_di_comp" disabled class="pointer-events-none di-indicator">
                                             <span>Compressed</span>
                                         </label>
                                         <label class="flex items-center gap-1 cursor-default">
                                             <input type="checkbox" id="live_di_dyn" disabled class="pointer-events-none di-indicator">
                                             <span>Dynamic PTY</span>
                                         </label>
                                     </div>
                                 </div>
                                 <div>
                                     <label>Status Flags</label>
                                     <div class="live-display sub flex flex-wrap gap-x-3 gap-y-1 text-xs">
                                         <span class="flex items-center gap-1">
                                             <span class="inline-block w-2 h-2 rounded-full" id="tp_indicator"></span>
                                             <span>TP</span>
                                         </span>
                                         <span class="flex items-center gap-1">
                                             <span class="inline-block w-2 h-2 rounded-full" id="ta_indicator"></span>
                                             <span>TA</span>
                                         </span>
                                         <span class="flex items-center gap-1">
                                             <span class="inline-block w-2 h-2 rounded-full" id="ms_indicator"></span>
                                             <span>M/S</span>
                                         </span>
                                     </div>
                                 </div>
                             </div>
                             
                             <div class="grid grid-cols-2 gap-2">
                                 <div>
                                     <label>Alternative Frequencies</label>
                                     <div class="live-display sub text-xs whitespace-pre-line" id="live_af"></div>
                                 </div>
                                 <div>
                                     <label>EON Networks</label>
                                     <div class="live-display sub text-xs whitespace-pre-line" id="live_eon"></div>
                                 </div>
                             </div>
                        </div>
                    </div>
                    
                    <div class="section col-span-2" id="rds2_status_section" style="display:none">
                        <div class="section-header">RDS2 Status</div>
                        <div class="section-body">
                            <div class="grid grid-cols-2 gap-4">
                                <div>
                                    <label class="flex justify-between">
                                        <span>Carrier Status</span>
                                        <span class="text-xs text-gray-400" id="rds2_carrier_count">0 active</span>
                                    </label>
                                    <div class="live-display sub text-xs" id="rds2_carriers">No carriers active</div>
                                    
                                    <div id="rds2_carrier_details" class="mt-2 grid grid-cols-3 gap-2" style="display:none">
                                        <div>
                                            <label class="text-xs">66.5 kHz</label>
                                            <div class="live-display sub text-xs text-center" id="rds2_c1_level">0%</div>
                                        </div>
                                        <div>
                                            <label class="text-xs">71.25 kHz</label>
                                            <div class="live-display sub text-xs text-center" id="rds2_c2_level">0%</div>
                                        </div>
                                        <div>
                                            <label class="text-xs">76 kHz</label>
                                            <div class="live-display sub text-xs text-center" id="rds2_c3_level">0%</div>
                                        </div>
                                    </div>
                                </div>
                                <div>
                                    <label>Logo/Image Being Transmitted</label>
                                    <div class="live-display sub text-xs" id="rds2_logo_name">No logo loaded</div>
                                    <div class="mt-2" id="rds2_logo_preview_container" style="display:none">
                                        <img id="rds2_logo_preview" src="" alt="RDS2 Logo" class="max-w-full h-auto border border-gray-600 rounded" style="max-height: 150px;">
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="section col-span-2" id="ari_status_section" style="display:none">
                        <div class="section-header">ARI Traffic Radio Status</div>
                        <div class="section-body">
                            <div class="grid grid-cols-3 gap-4">
                                <div>
                                    <label>Region (BK)</label>
                                    <div class="live-display sub text-xs" id="ari_region_display">-</div>
                                    <div class="text-[9px] text-gray-500 mt-1">
                                        <span id="ari_bk_freq_display">0 Hz</span>
                                    </div>
                                </div>
                                <div>
                                    <label>Announcement (DK)</label>
                                    <div class="live-display sub text-xs" id="ari_announcement_display">Inactive</div>
                                    <div class="text-[9px] text-gray-500 mt-1">125 Hz modulation</div>
                                </div>
                                <div>
                                    <label>Mode</label>
                                    <div class="live-display sub text-xs" id="ari_mode_display">Manual</div>
                                    <div class="text-[9px] text-gray-500 mt-1">Control mode</div>
                                </div>
                            </div>
                            <div class="mt-3 text-[10px] text-cyan-400 bg-[#0a1a1a] border border-cyan-900/50 rounded p-2">
                                ℹ️ ARI is a separate 57 kHz carrier with AM (SK=carrier, BK=region freq, DK=125Hz announcement)
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div id="basic" class="content">
                <div class="section">
                    <div class="section-header">Dynamic PS (8-Char)</div>
                    <div class="section-body">
                         <div class="flex justify-between mb-1">
                             <label>Text Source (Supports \R, \w, 3s:, \HR\, \MN\, \S\)</label>
                             <div class="flex gap-2 items-center"><label>Centre</label><input type="checkbox" class="toggle-checkbox" id="ps_centered" {% if state.ps_centered %}checked{% endif %} onchange="sync()"></div>
                         </div>
                         <input type="text" id="ps_dynamic" value="{{state.ps_dynamic}}" onchange="sync()">
                         <div class="text-[9px] text-gray-400 mt-1">
                             Time placeholders: \HR\ (hour 00-23), \MN\ (minute 00-59), \S\ (second 00-59)
                         </div>
                         <div class="mt-2">
                             <label>PS Group Version</label>
                             <select id="ps_group_version" onchange="updatePSGroupWarning(); sync()">
                                 <option value="0A" {% if state.ps_group_version == "0A" %}selected{% endif %}>Group 0A (with AF support)</option>
                                 <option value="0B" {% if state.ps_group_version == "0B" %}selected{% endif %}>Group 0B (PI in Block 3)</option>
                             </select>
                             <div id="ps_0b_warning" class="text-[10px] text-yellow-400 mt-1" style="display: {% if state.ps_group_version == '0B' %}block{% else %}none{% endif %};">
                                 ⚠️ Warning: Alternative Frequencies (AF) cannot be sent when using Group 0B
                             </div>
                         </div>
                    </div>
                </div>
                
                <div class="section">
                    <div class="section-header">Station Identification</div>
                    <div class="section-body grid-cols-2">
                        <div><label>PI Code (Hex)</label><input type="text" id="pi" value="{{state.pi}}" maxlength="4" class="font-mono text-center tracking-widest" onchange="sync()"></div>
                        <div>
                            <div class="flex justify-between items-center mb-1">
                                <label>Program Type (PTY)</label>
                                <label class="flex items-center gap-1 text-xs cursor-pointer">
                                    <input type="checkbox" id="rbds" class="toggle-checkbox" {% if state.rbds %}checked{% endif %} onchange="updatePTYList()">
                                    <span>RBDS</span>
                                </label>
                            </div>
                            <select id="pty" onchange="sync()">
                                {% for p in pty_list_rds %}<option value="{{loop.index0}}" {% if loop.index0 == state.pty %}selected{% endif %} data-rds="{{p}}" data-rbds="{{pty_list_rbds[loop.index0]}}">{{p}}</option>{% endfor %}
                            </select>
                        </div>
                    </div>
                </div>

                <div class="section">
                    <div class="section-header">PTYN</div>
                    <div class="section-body">
                         <div class="flex gap-3 items-center">
                            <div class="flex gap-2 items-center"><label>Enable</label><input type="checkbox" class="toggle-checkbox" id="en_ptyn" {% if state.en_ptyn %}checked{% endif %} onchange="sync()"></div>
                            <div class="flex gap-2 items-center"><label>Centre Text</label><input type="checkbox" class="toggle-checkbox" id="ptyn_centered" {% if state.ptyn_centered %}checked{% endif %} onchange="sync()"></div>
                         </div>
                         <input type="text" id="ptyn" value="{{state.ptyn}}" onchange="sync()">
                    </div>
                 </div>

                 <div class="section">
                    <div class="section-header">Long PS (Group 15)</div>
                    <div class="section-body">
                         <div class="flex gap-3 items-center">
                             <div class="flex gap-2 items-center"><label>Enable</label><input type="checkbox" class="toggle-checkbox" id="en_lps" {% if state.en_lps %}checked{% endif %} onchange="sync()"></div>
                             <div class="flex gap-2 items-center"><label>Centre Text</label><input type="checkbox" class="toggle-checkbox" id="lps_centered" {% if state.lps_centered %}checked{% endif %} onchange="sync()"></div>
                             <div class="flex gap-2 items-center"><label>Append CR</label><input type="checkbox" class="toggle-checkbox" id="lps_cr" {% if state.lps_cr %}checked{% endif %} onchange="sync()"></div>
                         </div>
                         <input type="text" id="ps_long_32" value="{{state.ps_long_32}}" onchange="sync()">
                    </div>
                 </div>

                <div class="section">
                    <div class="section-header flex justify-between items-center">
                        <span>RadioText Messages</span>
                        <button onclick="addRTMessage()" class="px-2 py-1 bg-green-700 hover:bg-green-600 rounded text-xs text-white font-bold">+ Add Message</button>
                    </div>
                    <div class="section-body">
                        <!-- Message List -->
                        <div id="rt_messages_list" class="space-y-2 mb-3">
                            <!-- Messages rendered by JavaScript -->
                        </div>

                        <!-- Empty state -->
                        <div id="rt_messages_empty" class="text-center py-4 text-gray-500 text-sm" style="display:none">
                            No messages configured. Click "+ Add Message" to create one.
                        </div>

                        <!-- Global RT Settings -->
                        <div class="flex justify-between items-center bg-[#111] p-2 rounded mt-3">
                             <div class="flex gap-4 items-end">
                                 <div>
                                     <label>Mode</label>
                                     <select id="rt_mode" onchange="sync()"><option value="2A" {% if state.rt_mode == '2A' %}selected{% endif %}>2A (64)</option><option value="2B" {% if state.rt_mode == '2B' %}selected{% endif %}>2B (32)</option></select>
                                 </div>
                             </div>
                             <div class="flex gap-4">
                                 <div class="flex flex-col items-center"><label>RT+ Enable</label><input type="checkbox" class="toggle-checkbox" id="en_rt_plus" {% if state.en_rt_plus %}checked{% endif %} onchange="sync()"></div>
                                 <div class="flex flex-col items-center"><label>Centre</label><input type="checkbox" class="toggle-checkbox" id="rt_centered" {% if state.rt_centered %}checked{% endif %} onchange="if(this.checked) document.getElementById('rt_cr').checked = false; sync()"></div>
                                 <div class="flex flex-col items-center"><label>Append CR</label><input type="checkbox" class="toggle-checkbox" id="rt_cr" {% if state.rt_cr %}checked{% endif %} onchange="sync()"></div>
                                 <div class="flex flex-col items-center"><label>Disable 0D</label><input type="checkbox" class="toggle-checkbox" id="rt_disable_0d" {% if state.rt_disable_0d %}checked{% endif %} onchange="sync()"></div>
                             </div>
                        </div>
                    </div>
                </div>

                <!-- RT Message Edit Modal -->
                <div id="rt_msg_modal" class="rtplus-modal-overlay" style="display: none;">
                    <div class="rtplus-modal" style="max-width: 750px;">
                        <div class="rtplus-modal-header">
                            <div>
                                <div class="text-lg font-bold text-white" id="rt_msg_modal_title">Edit Message</div>
                                <div class="text-xs text-gray-400">Configure RT message content and RT+ tags</div>
                            </div>
                            <button onclick="closeRTMsgModal()" class="text-gray-400 hover:text-white text-2xl leading-none">&times;</button>
                        </div>

                        <div class="rtplus-modal-body space-y-4">
                            <input type="hidden" id="rt_msg_edit_id">

                            <!-- Buffer Selection -->
                            <div>
                                <label class="text-xs text-gray-400 mb-1 block">Buffer</label>
                                <div class="flex gap-2">
                                    <button type="button" id="rt_msg_buf_a" onclick="setMsgBuffer('A')" class="px-4 py-2 rounded font-bold text-sm bg-[#333] text-gray-400 hover:bg-[#444]">A</button>
                                    <button type="button" id="rt_msg_buf_b" onclick="setMsgBuffer('B')" class="px-4 py-2 rounded font-bold text-sm bg-[#333] text-gray-400 hover:bg-[#444]">B</button>
                                    <button type="button" id="rt_msg_buf_ab" onclick="setMsgBuffer('AB')" class="px-4 py-2 rounded font-bold text-sm bg-[#7c3aed] text-white">A+B</button>
                                    <button type="button" id="rt_msg_buf_auto" onclick="setMsgBuffer('AUTO')" class="px-4 py-2 rounded font-bold text-sm bg-[#333] text-gray-400 hover:bg-[#444]" title="Flip A&#x2194;B automatically when content changes">AUTO</button>
                                </div>
                            </div>

                            <!-- Cycles -->
                            <div>
                                <label class="text-xs text-gray-400 mb-1 block">Cycles</label>
                                <input type="number" id="rt_msg_cycles" value="2" min="1" max="50" class="w-24 bg-[#111] border border-[#444] rounded px-2 py-1">
                            </div>

                            <!-- Source Type -->
                            <div>
                                <label class="text-xs text-gray-400 mb-1 block">Source Type</label>
                                <div class="flex gap-4">
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <input type="radio" name="rt_msg_source" value="manual" checked class="accent-[#d946ef]" onchange="updateSourceUI()">
                                        <span class="text-sm text-gray-300">Manual</span>
                                    </label>
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <input type="radio" name="rt_msg_source" value="file" class="accent-[#d946ef]" onchange="updateSourceUI()">
                                        <span class="text-sm text-gray-300">File</span>
                                    </label>
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <input type="radio" name="rt_msg_source" value="url" class="accent-[#d946ef]" onchange="updateSourceUI()">
                                        <span class="text-sm text-gray-300">URL</span>
                                    </label>
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <input type="radio" name="rt_msg_source" value="json" class="accent-[#d946ef]" onchange="updateSourceUI()">
                                        <span class="text-sm text-gray-300">Web (JSON)</span>
                                    </label>
                                </div>
                            </div>

                            <!-- Content for File/URL -->
                            <div id="rt_msg_content_wrap" style="display:none">
                                <div class="space-y-2">
                                    <div>
                                        <label class="text-xs text-gray-400 mb-1 block" id="rt_msg_content_label">Content Source</label>
                                        <input type="text" id="rt_msg_content" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1" placeholder="Enter file path or URL" oninput="updateMsgPreview()">
                                        <div class="text-[10px] text-gray-500 mt-1" id="rt_msg_content_hint">Full path to file or URL</div>
                                    </div>
                                    <div class="grid grid-cols-2 gap-2">
                                        <div>
                                            <label class="text-xs text-gray-400 mb-1 block">Prefix (optional)</label>
                                            <input type="text" id="rt_msg_prefix_auto" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" placeholder="Text before content" oninput="updateMsgPreview()">
                                        </div>
                                        <div>
                                            <label class="text-xs text-gray-400 mb-1 block">Suffix (optional)</label>
                                            <input type="text" id="rt_msg_suffix_auto" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" placeholder="Text after content" oninput="updateMsgPreview()">
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <!-- Content for JSON -->
                            <div id="rt_msg_json_wrap" style="display:none">
                                <div class="space-y-3">
                                    <div>
                                        <label class="text-xs text-gray-400 mb-1 block">JSON URL</label>
                                        <div class="flex gap-2">
                                            <input type="text" id="rt_msg_json_url" class="flex-1 bg-[#111] border border-[#444] rounded px-2 py-1" placeholder="https://api.example.com/nowplaying">
                                            <button onclick="fetchAndAnalyzeJSON()" class="px-3 py-1 bg-[#d946ef] hover:bg-[#c026d3] rounded text-xs text-white font-bold whitespace-nowrap">Fetch & Analyze</button>
                                        </div>
                                        <div class="text-[10px] text-gray-500 mt-1">URL to JSON endpoint</div>
                                    </div>

                                    <!-- JSON Structure Display -->
                                    <div id="rt_msg_json_structure" style="display:none" class="bg-[#0a0a0a] border border-[#333] rounded p-3 space-y-2">
                                        <div class="text-xs text-green-400 font-bold mb-2">Available JSON Fields:</div>
                                        <div id="rt_msg_json_fields" class="text-xs text-gray-400 space-y-1 max-h-32 overflow-y-auto">
                                            <!-- Fields will be populated here -->
                                        </div>
                                    </div>

                                    <!-- JSON Field Mapping -->
                                    <div id="rt_msg_json_config" style="display:none" class="space-y-3 bg-[#0a0a0a] border border-[#333] rounded p-3">
                                        <div class="text-xs text-cyan-400 font-bold mb-2">📍 Map JSON Fields to Tags</div>
                                        <div class="grid grid-cols-2 gap-3">
                                            <div>
                                                <label class="text-xs text-orange-400 font-bold">Field 1</label>
                                                <select id="rt_msg_json_field1" class="w-full bg-[#111] border border-orange-900/50 rounded px-2 py-1 text-xs" onchange="updateMsgPreview()">
                                                    <option value="">Select field...</option>
                                                </select>
                                            </div>
                                            <div>
                                                <label class="text-xs text-orange-400 font-bold">Tag Type</label>
                                                <select id="rt_msg_json_tag1_type" class="w-full bg-[#111] border border-orange-900/50 rounded px-2 py-1 text-xs" onchange="updateMsgPreview()">
                                                    <!-- Populated by populateRTPlusSelect() -->
                                                </select>
                                            </div>
                                        </div>
                                        <div class="flex items-center gap-2 pl-1">
                                            <input type="checkbox" id="rt_msg_json_hide_if_blank" class="accent-orange-600" onchange="updateMsgPreview()">
                                            <label class="text-xs text-gray-400">Hide Field 1 if blank (won't show separator)</label>
                                        </div>
                                        <div class="grid grid-cols-2 gap-3">
                                            <div>
                                                <label class="text-xs text-cyan-400 font-bold">Field 2</label>
                                                <select id="rt_msg_json_field2" class="w-full bg-[#111] border border-cyan-900/50 rounded px-2 py-1 text-xs" onchange="updateMsgPreview()">
                                                    <option value="">None (single field)</option>
                                                </select>
                                            </div>
                                            <div>
                                                <label class="text-xs text-cyan-400 font-bold">Tag Type</label>
                                                <select id="rt_msg_json_tag2_type" class="w-full bg-[#111] border border-cyan-900/50 rounded px-2 py-1 text-xs" onchange="updateMsgPreview()">
                                                    <!-- Populated by populateRTPlusSelect() -->
                                                </select>
                                            </div>
                                        </div>
                                        <div>
                                            <label class="text-xs text-gray-400">Separator (between fields)</label>
                                            <input type="text" id="rt_msg_json_delimiter" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs" value=" - " onchange="updateMsgPreview()">
                                        </div>
                                    </div>

                                </div>
                            </div>

                            <!-- Manual Mode: Simple Text (when RT+ disabled) -->
                            <div id="rt_msg_manual_simple" style="display:none">
                                <div>
                                    <label class="text-xs text-gray-400 mb-1 block">RadioText Content</label>
                                    <input type="text" id="rt_msg_simple_text" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1" placeholder="Enter RadioText message (max 64 chars)" maxlength="64" oninput="updateMsgPreview()">
                                    <div class="text-[10px] text-gray-500 mt-1">Character count: <span id="rt_msg_simple_count">0</span>/64</div>
                                </div>
                            </div>

                            <!-- Manual Mode: Sample Text Input (when RT+ enabled) -->
                            <div id="rt_msg_manual_builder" style="display:none" class="space-y-3">
                                <div>
                                    <label class="text-xs text-gray-400 mb-1 block">RadioText Content</label>
                                    <input type="text" id="rt_msg_sample_text" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1" oninput="updateSampleText(); updateMsgPreview()">
                                </div>
                            </div>

                            <!-- RT+ Configuration (common for all modes) -->
                            <div class="bg-[#1a1a1a] border border-[#333] rounded p-3 space-y-3">
                                <div class="flex justify-between items-center">
                                    <label class="text-xs text-orange-400 font-bold">RT+ Tags</label>
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <span class="text-xs text-gray-400">Enable for this message</span>
                                        <input type="checkbox" id="rt_msg_rtplus_enabled" class="toggle-checkbox" onchange="updateRTPlusUI()">
                                    </label>
                                </div>

                                <!-- RT+ Tagging Policies -->
                                <div id="rt_msg_rtplus_options" style="display:none" class="space-y-4">
                                    <!-- Policy Manager Header -->
                                    <div class="bg-[#252525] border border-blue-600/50 rounded p-3">
                                        <div class="flex items-center justify-between mb-3">
                                            <div class="flex items-center gap-2">
                                                <div class="text-blue-400 font-bold">🎯 Tagging Policies</div>
                                                <div class="text-xs text-blue-300">Define how content gets tagged</div>
                                            </div>
                                            <button onclick="addTaggingPolicy()" class="px-3 py-1 bg-blue-800 hover:bg-blue-700 rounded text-xs text-white font-bold">+ Add Policy</button>
                                        </div>
                                        
                                        <!-- Policies List -->
                                        <div id="tagging_policies_list" class="space-y-2">
                                            <!-- Policies will be rendered here -->
                                        </div>
                                        
                                        <div class="text-[9px] text-gray-500 mt-3 border-t border-gray-700 pt-2">
                                            💡 <strong>Default</strong>: Base tagging settings • <strong>Sub-tagging</strong>: Conditional rules for specific patterns • Evaluated top-to-bottom
                                        </div>
                                    </div>

                                    <!-- Policy Editor Modal (will be shown when editing) -->
                                    <div id="policy_editor" style="display:none" class="bg-[#1a1a1a] border border-purple-600/50 rounded p-4 space-y-3">
                                        <div class="flex items-center justify-between">
                                            <h3 class="text-purple-400 font-bold" id="policy_editor_title">Edit Policy</h3>
                                            <button onclick="closePolicyEditor()" class="text-gray-400 hover:text-white">✕</button>
                                        </div>
                                        
                                        <div class="grid grid-cols-2 gap-3">
                                            <div>
                                                <label class="text-xs text-gray-400 block mb-1">Policy Name</label>
                                                <input type="text" id="policy_name" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" placeholder="e.g. Default Music, Breaking News">
                                            </div>
                                            <div>
                                                <label class="text-xs text-gray-400 block mb-1">Policy Type</label>
                                                <select id="policy_type" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" onchange="updatePolicyEditor()">
                                                    <option value="default">Default</option>
                                                    <option value="sub">Sub-tagging</option>
                                                </select>
                                            </div>
                                        </div>

                                        <!-- Default Policy Settings -->
                                        <div id="default_policy_settings" class="space-y-3">
                                            <div class="text-xs text-green-400 font-bold">📍 Default Tagging Settings</div>
                                            <div class="grid grid-cols-2 gap-3">
                                                <div>
                                                    <label class="text-xs text-orange-400">Tag 1 Type</label>
                                                    <select id="policy_default_tag1" class="w-full bg-[#111] border border-orange-900/50 rounded px-2 py-1 text-xs">
                                                        <option value="1">1: Title</option>
                                                        <option value="4" selected>4: Artist</option>
                                                        <option value="2">2: Album</option>
                                                        <option value="33">33: Prog.Now</option>
                                                        <option value="31">31: Stn.Short</option>
                                                        <option value="32">32: Stn.Long</option>
                                                        <option value="-1">No Tag</option>
                                                    </select>
                                                </div>
                                                <div>
                                                    <label class="text-xs text-cyan-400">Tag 2 Type</label>
                                                    <select id="policy_default_tag2" class="w-full bg-[#111] border border-cyan-900/50 rounded px-2 py-1 text-xs">
                                                        <option value="1" selected>1: Title</option>
                                                        <option value="4">4: Artist</option>
                                                        <option value="2">2: Album</option>
                                                        <option value="33">33: Prog.Now</option>
                                                        <option value="32">32: Stn.Long</option>
                                                        <option value="36">36: Host</option>
                                                        <option value="-1">No Tag</option>
                                                    </select>
                                                </div>
                                            </div>
                                            <div class="grid grid-cols-3 gap-3">
                                                <div>
                                                    <label class="text-xs text-gray-400">Split Pattern</label>
                                                    <input type="text" id="policy_default_split" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs" value=" - " placeholder="e.g. ' - '">
                                                </div>
                                                <div>
                                                    <label class="text-xs text-gray-400">Prefix</label>
                                                    <input type="text" id="policy_default_prefix" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs" placeholder="Text before">
                                                </div>
                                                <div>
                                                    <label class="text-xs text-gray-400">Suffix</label>
                                                    <input type="text" id="policy_default_suffix" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs" placeholder="Text after">
                                                </div>
                                            </div>
                                        </div>

                                        <!-- Sub-tagging Policy Settings -->
                                        <div id="sub_policy_settings" style="display:none" class="space-y-3">
                                            <!-- Content Trigger (optional pre-condition) -->
                                            <div class="text-xs text-cyan-400 font-bold">🎯 Content Trigger (Optional)</div>
                                            <div class="bg-[#1a1a1a] p-3 rounded border border-cyan-900/30 space-y-2">
                                                <div class="text-xs text-gray-400 mb-2">Only apply this policy if the content matches this trigger:</div>
                                                <div class="grid grid-cols-2 gap-3">
                                                    <div>
                                                        <label class="text-xs text-gray-400">Trigger Type</label>
                                                        <select id="policy_sub_trigger_type" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs">
                                                            <option value="none">No Trigger (Always Apply)</option>
                                                            <option value="contains">Content contains text</option>
                                                            <option value="starts_with">Content starts with text</option>
                                                            <option value="ends_with">Content ends with text</option>
                                                            <option value="equals">Content exactly equals</option>
                                                            <option value="regex">Content matches regex</option>
                                                        </select>
                                                    </div>
                                                    <div>
                                                        <label class="text-xs text-gray-400">Trigger Pattern</label>
                                                        <input type="text" id="policy_sub_trigger_pattern" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs" placeholder="e.g. Station Name, Live">
                                                    </div>
                                                </div>
                                            </div>
                                            
                                            <!-- Pattern Matching -->
                                            <div class="text-xs text-purple-400 font-bold">⚡ Sub-tagging Condition</div>
                                            <div class="grid grid-cols-3 gap-3">
                                                <div>
                                                    <label class="text-xs text-gray-400">Condition</label>
                                                    <select id="policy_sub_condition" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs">
                                                        <option value="starts_with">Starts with</option>
                                                        <option value="ends_with">Ends with</option>
                                                        <option value="contains">Contains</option>
                                                        <option value="equals">Exactly equals</option>
                                                        <option value="regex">Regex pattern</option>
                                                    </select>
                                                </div>
                                                <div>
                                                    <label class="text-xs text-gray-400">Pattern</label>
                                                    <input type="text" id="policy_sub_pattern" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs" placeholder="e.g. +++, Live:, News">
                                                </div>
                                                <div>
                                                    <label class="text-xs text-gray-400">Tag Action</label>
                                                    <select id="policy_sub_action" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs">
                                                        <option value="tag_all">Tag entire content</option>
                                                        <option value="tag_after">Tag content after pattern</option>
                                                        <option value="tag_before">Tag content before pattern</option>
                                                        <option value="tag_match">Tag only the match</option>
                                                    </select>
                                                </div>
                                            </div>
                                            <div class="grid grid-cols-2 gap-3">
                                                <div>
                                                    <label class="text-xs text-orange-400">Tag Type</label>
                                                    <select id="policy_sub_tag_type" class="w-full bg-[#111] border border-orange-900/50 rounded px-2 py-1 text-xs">
                                                        <option value="1">1: Title</option>
                                                        <option value="4">4: Artist</option>
                                                        <option value="2">2: Album</option>
                                                        <option value="33">33: Prog.Now</option>
                                                        <option value="31">31: Stn.Short</option>
                                                        <option value="32">32: Stn.Long</option>
                                                        <option value="36">36: Host</option>
                                                        <option value="12">12: Info.News</option>
                                                    </select>
                                                </div>
                                                <div>
                                                    <label class="text-xs text-gray-400">Strip Pattern</label>
                                                    <input type="checkbox" id="policy_sub_strip_pattern" class="accent-purple-600">
                                                    <span class="text-xs text-gray-300 ml-1">Remove pattern from tagged text</span>
                                                </div>
                                            </div>
                                        </div>

                                        <!-- Policy Actions -->
                                        <div class="flex gap-2 pt-3 border-t border-gray-700">
                                            <button onclick="savePolicyEditor()" class="px-4 py-2 bg-green-800 hover:bg-green-700 rounded text-sm text-white font-bold">Save Policy</button>
                                            <button onclick="closePolicyEditor()" class="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm text-white">Cancel</button>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <!-- Text Processing / Trim Options -->
                            <div class="bg-[#1a1a1a] border border-[#333] rounded p-3 space-y-2">
                                <label class="text-xs text-teal-400 font-bold block">✂️ Text Processing</label>
                                <div class="grid grid-cols-2 gap-2 text-xs">
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <input type="checkbox" id="rt_msg_trim_parens" class="accent-teal-600">
                                        <span class="text-gray-300">Remove (...) parentheses</span>
                                    </label>
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <input type="checkbox" id="rt_msg_trim_brackets" class="accent-teal-600">
                                        <span class="text-gray-300">Remove [...] brackets</span>
                                    </label>
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <input type="checkbox" id="rt_msg_trim_semicolon" class="accent-teal-600">
                                        <span class="text-gray-300">Trim at ; per segment</span>
                                    </label>
                                    <label class="flex items-center gap-2 cursor-pointer">
                                        <input type="checkbox" id="rt_msg_trim_maxlen" class="accent-teal-600">
                                        <span class="text-gray-300">Truncate to 64 chars</span>
                                    </label>
                                </div>
                            </div>

                            <!-- Preview -->
                            <div class="bg-[#000] border border-[#333] rounded p-3">
                                <div class="flex justify-between items-center mb-2">
                                    <div class="flex items-center gap-2">
                                        <label class="text-xs text-gray-400 font-bold">PREVIEW</label>
                                        <span class="text-gray-500">-</span>
                                        <span id="rt_msg_rule_applied" class="text-gray-400 text-xs">Current policy: Field Mapping</span>
                                    </div>
                                    <div class="text-xs">
                                        <span id="rt_msg_char_count" class="text-green-400">0</span>
                                        <span class="text-gray-500">/</span>
                                        <span id="rt_msg_char_limit" class="text-gray-400">64</span>
                                        <span class="text-gray-600 ml-1">chars</span>
                                    </div>
                                </div>
                                <div id="rt_msg_preview" class="font-mono text-base text-gray-300 min-h-[24px] whitespace-pre leading-relaxed"></div>
                                <div class="mt-2 grid grid-cols-2 gap-2 text-xs" id="rt_msg_tag_info">
                                    <div class="text-orange-400">Tag 1: <span id="rt_msg_tag1_info" class="text-orange-300">-</span></div>
                                    <div class="text-cyan-400">Tag 2: <span id="rt_msg_tag2_info" class="text-cyan-300">-</span></div>
                                </div>
                                <div class="mt-1 font-mono text-base text-gray-600 overflow-x-auto whitespace-nowrap">
                                    0----5----10---15---20---25---30---35---40---45---50---55---60---
                                </div>
                            </div>
                        </div>

                        <div class="rtplus-modal-footer">
                            <button onclick="closeRTMsgModal()" class="px-4 py-2 bg-[#333] hover:bg-[#444] rounded text-sm text-gray-300">Cancel</button>
                            <button onclick="saveRTMessage()" class="px-4 py-2 bg-[#d946ef] hover:bg-[#c026d3] rounded text-sm text-white font-bold">Save</button>
                        </div>
                    </div>
                </div>

                <!-- AF Pair Modal -->
                <div id="af_pair_modal" class="rtplus-modal-overlay" style="display: none;">
                    <div class="rtplus-modal-content" style="max-width: 500px;">
                        <div class="flex justify-between items-center mb-4">
                            <h3 id="af_pair_modal_title" class="text-lg font-bold">Add Frequency Pair</h3>
                            <button onclick="closeAFPairModal()" class="text-2xl leading-none hover:text-[#d946ef]">×</button>
                        </div>

                        <input type="hidden" id="af_pair_edit_id" value="">

                        <div class="space-y-4">
                            <!-- Main Frequency -->
                            <div>
                                <label class="text-xs text-gray-400 mb-1 block">Main Frequency (MHz)</label>
                                <input type="text" id="af_pair_main" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1" placeholder="99.5">
                                <div class="text-[10px] text-gray-500 mt-1">The tuning frequency</div>
                            </div>

                            <!-- Alternative Frequencies -->
                            <div>
                                <label class="text-xs text-gray-400 mb-1 block">Alternative Frequencies (MHz)</label>
                                <textarea id="af_pair_alts" rows="3" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1" placeholder="89.3, 101.7, 88.1"></textarea>
                                <div class="text-[10px] text-gray-500 mt-1">Comma-separated list of frequencies carrying same/regional variants</div>
                            </div>

                            <!-- Regional Variant Flag -->
                            <div class="flex items-center justify-between bg-[#111] border border-[#444] rounded px-3 py-2">
                                <div>
                                    <label class="text-xs text-gray-400">Regional Variant (RV)</label>
                                    <div class="text-[10px] text-gray-500 mt-1">Check if these frequencies carry different regional programming</div>
                                </div>
                                <input type="checkbox" class="toggle-checkbox" id="af_pair_regional">
                            </div>
                        </div>

                        <div class="flex justify-end gap-2 mt-6">
                            <button onclick="closeAFPairModal()" class="px-4 py-2 bg-[#444] hover:bg-[#555] rounded text-sm">Cancel</button>
                            <button onclick="saveAFPair()" class="px-4 py-2 bg-[#7c3aed] hover:bg-[#6d28d9] rounded text-sm text-white font-bold">Save</button>
                        </div>
                    </div>
                </div>

                <div class="section">
                    <div class="section-header">Flags & Switches</div>
                    <div class="section-body grid-cols-4">
                        <div class="flex flex-col items-center"><label>Traffic Prog</label><input type="checkbox" class="toggle-checkbox" id="tp" {% if state.tp %}checked{% endif %} onchange="sync()"></div>
                        <div class="flex flex-col items-center"><label>Traffic Ann</label><input type="checkbox" class="toggle-checkbox" id="ta" {% if state.ta %}checked{% endif %} onchange="sync()"></div>
                        <div class="flex flex-col items-center"><label>Music/Speech</label><input type="checkbox" class="toggle-checkbox" id="ms" {% if state.ms %}checked{% endif %} onchange="sync()"></div>
                        <div class="flex flex-col items-center"><label>Stereo</label><input type="checkbox" class="toggle-checkbox" id="di_stereo" {% if state.di_stereo %}checked{% endif %} onchange="sync()"></div>
                        <div class="flex flex-col items-center"><label>Artificial Head</label><input type="checkbox" class="toggle-checkbox" id="di_head" {% if state.di_head %}checked{% endif %} onchange="sync()"></div>
                        <div class="flex flex-col items-center"><label>Compressed</label><input type="checkbox" class="toggle-checkbox" id="di_comp" {% if state.di_comp %}checked{% endif %} onchange="sync()"></div>
                        <div class="flex flex-col items-center"><label>Dynamic PTY</label><input type="checkbox" class="toggle-checkbox" id="di_dyn" {% if state.di_dyn %}checked{% endif %} onchange="sync()"></div>
                    </div>
                </div>
                
                <div class="section">
                    <div class="section-header flex justify-between items-center">
                        <span>Alternative Frequencies (AF)</span>
                        <label class="flex items-center gap-2 cursor-pointer">
                            <span class="text-xs">Enable</span>
                            <input type="checkbox" class="toggle-checkbox" id="en_af" {% if state.en_af %}checked{% endif %} onchange="sync()">
                        </label>
                    </div>
                    <div class="section-body">
                         <div class="mb-3">
                             <label class="text-xs text-gray-400 mb-1 block">AF Method</label>
                             <select id="af_method" class="w-full" onchange="updateAFMethodUI(); sync();">
                                 <option value="A" {% if state.af_method == "A" %}selected{% endif %}>Method A - Simple list</option>
                                 <option value="B" {% if state.af_method == "B" %}selected{% endif %}>Method B - Frequency pairs</option>
                             </select>
                         </div>

                         <!-- Method A: Simple list -->
                         <div id="af_method_a_ui" style="display:none">
                             <label class="text-xs text-gray-400 mb-1 block">Frequency List (MHz)</label>
                             <textarea id="af_list" rows="2" placeholder="87.5, 98.1, 104.2" onchange="sync()">{{state.af_list}}</textarea>
                             <div class="text-[10px] text-gray-500 mt-1">Comma-separated list of all frequencies</div>
                         </div>

                         <!-- Method B: Frequency pairs -->
                         <div id="af_method_b_ui" style="display:none">
                             <div class="flex justify-between items-center mb-2">
                                 <span class="text-xs text-gray-400">Frequency Pairs</span>
                                 <button onclick="addAFPair()" class="px-2 py-1 bg-[#7c3aed] hover:bg-[#6d28d9] text-white text-xs rounded">+ Add Pair</button>
                             </div>
                             <div id="af_pairs_list" class="space-y-2"></div>
                             <div id="af_pairs_empty" class="text-center py-4 text-gray-600 text-sm" style="display:none">
                                 No frequency pairs configured. Click "+ Add Pair" to create one.
                             </div>
                         </div>
                    </div>
                </div>
            </div>

            <div id="expert" class="content">
                 <div class="section">
                    <div class="section-header">Scheduler Sequence</div>
                    <div class="section-body">
                         <div class="flex justify-between mb-2">
                             <label>Sequence String (e.g. 0A 0A 2A)</label>
                             <div class="flex items-center gap-2">
                                 <span class="text-[9px] text-gray-400">Manual / Auto</span>
                                 <input type="checkbox" class="toggle-checkbox" id="scheduler_auto" {% if state.scheduler_auto %}checked{% endif %} onchange="sync()">
                             </div>
                         </div>
                         <input type="text" id="group_sequence" value="{{state.group_sequence}}" onchange="sync()" class="font-mono">
                         <div class="text-[9px] text-gray-500 mt-1">
                             Auto Mode intelligently injects active groups (LPS, PTYN, ID, RT+) alongside standard PS/RT.
                         </div>
                    </div>
                </div>

                 <div class="section">
                     <div class="section-header">System IDs</div>
                     <div class="section-body grid-cols-2">
                         <div><label>ECC</label><input type="text" id="ecc" value="{{state.ecc}}" onchange="sync()"></div>
                         <div><label>LIC</label><input type="text" id="lic" value="{{state.lic}}" onchange="sync()"></div>
                         <div><label>Clock Offset (Fine Tuning)</label><input type="number" id="tz_offset" value="{{state.tz_offset}}" onchange="sync()" step="0.5"></div>
                         <div class="flex items-center gap-2"><label class="flex-1">Enable CT</label><input type="checkbox" class="toggle-checkbox" id="en_ct" {% if state.en_ct %}checked{% endif %} onchange="sync()"></div>
                         <div class="flex items-center gap-2"><label class="flex-1">Enable ID (1A)</label><input type="checkbox" class="toggle-checkbox" id="en_id" {% if state.en_id %}checked{% endif %} onchange="sync()"></div>
                     </div>
                 </div>

                 <div class="section">
                    <div class="section-header flex justify-between items-center">
                        <span>Enhanced RadioText (eRT) Messages</span>
                        <button id="ert_add_msg_btn" onclick="addERTMessage()" class="px-2 py-1 bg-green-700 hover:bg-green-600 rounded text-xs text-white font-bold">+ Add Message</button>
                    </div>
                    <div class="section-body">
                        <div class="mb-3">
                            <div class="flex items-start gap-2 mb-2">
                                <div class="flex-1">
                                    <label>Enable eRT</label>
                                    <div class="text-[9px] text-gray-500">UTF-8/UCS-2 RadioText with 128-byte capacity (AID: 0x6552)</div>
                                </div>
                                <input type="checkbox" class="toggle-checkbox" id="en_ert" {% if state.en_ert %}checked{% endif %} onchange="sync()">
                            </div>
                            <div class="flex items-center gap-3 mt-2">
                                <label class="text-xs text-gray-400 shrink-0">Content Source</label>
                                <select id="ert_source" onchange="sync(); updateERTSourceUI()">
                                    <option value="ert" {% if state.ert_source != 'rt' %}selected{% endif %}>eRT Messages</option>
                                    <option value="rt" {% if state.ert_source == 'rt' %}selected{% endif %}>RT Message Manager (UTF-8, 128 bytes)</option>
                                </select>
                            </div>
                        </div>

                        <!-- Source: eRT messages section -->
                        <div id="ert_msg_section">
                            <!-- Message List -->
                            <div id="ert_messages_list" class="space-y-2 mb-3">
                                <!-- Messages rendered by JavaScript -->
                            </div>

                            <!-- Empty state -->
                            <div id="ert_messages_empty" class="text-center py-4 text-gray-500 text-sm" style="display:none">
                                No messages configured. Click "+ Add Message" to create one.
                            </div>
                        </div>

                        <!-- Source: RT message manager note -->
                        <div id="ert_source_rt_note" style="display:none" class="mb-3 p-3 bg-[#0a1a0a] border border-green-900/50 rounded text-xs text-gray-400">
                            eRT will transmit the same content as the <span class="text-green-400 font-semibold">RT Message Manager</span>, re-encoded as UTF-8 up to 128 bytes. Cycling follows RT &mdash; when RT advances to the next message, eRT follows. Manage messages in the <span class="text-white">RadioText &rarr; RT Messages</span> section.
                        </div>

                        <!-- Global eRT Settings -->
                        <div class="flex justify-between items-center bg-[#111] p-2 rounded mt-3">
                             <div class="flex gap-4 items-end">
                                 <div>
                                     <label>Encoding</label>
                                     <select id="ert_encoding" onchange="sync()">
                                         <option value="utf8" {% if state.ert_encoding == "utf8" %}selected{% endif %}>UTF-8</option>
                                         <option value="ucs2" {% if state.ert_encoding == "ucs2" %}selected{% endif %}>UCS-2</option>
                                     </select>
                                 </div>
                                 <div>
                                     <label>Application Group Type</label>
                                     <select id="ert_group_type" onchange="sync()">
                                         {% for gt in range(6, 16) %}
                                         <option value="{{gt}}" {% if state.ert_group_type == gt %}selected{% endif %}>{{gt}}A</option>
                                         {% endfor %}
                                     </select>
                                 </div>
                             </div>
                             <div class="flex gap-4">
                                 <div class="flex flex-col items-center"><label>RT+ Enable</label><input type="checkbox" class="toggle-checkbox" id="en_ert_rtplus" {% if state.en_ert_rtplus %}checked{% endif %} onchange="sync()"></div>
                                 <div>
                                     <label>RT+ Group</label>
                                     <select id="ert_rtplus_group_type" onchange="sync()">
                                         {% for gt in range(4, 16) %}
                                         {% if gt != 11 %}
                                         <option value="{{gt}}" {% if state.ert_rtplus_group_type == gt %}selected{% endif %}>{{gt}}A</option>
                                         {% endif %}
                                         {% endfor %}
                                     </select>
                                 </div>
                             </div>
                        </div>
                     </div>
                 </div>

                 <!-- eRT Message Edit Modal -->
                 <div id="ert_msg_modal" class="rtplus-modal-overlay" style="display: none;">
                     <div class="rtplus-modal" style="max-width: 600px;">
                         <div class="rtplus-modal-header">
                             <div>
                                 <div class="text-lg font-bold text-white" id="ert_msg_modal_title">Edit eRT Message</div>
                                 <div class="text-xs text-gray-400">Configure enhanced RadioText message content</div>
                             </div>
                             <button onclick="closeERTMsgModal()" class="text-gray-400 hover:text-white text-2xl leading-none">&times;</button>
                         </div>

                         <div class="rtplus-modal-body space-y-4">
                             <input type="hidden" id="ert_msg_edit_id">

                             <!-- Cycles -->
                             <div>
                                 <label class="text-xs text-gray-400 mb-1 block">Cycles (How many times to display this message)</label>
                                 <input type="number" id="ert_msg_cycles" value="2" min="1" max="50" class="w-24 bg-[#111] border border-[#444] rounded px-2 py-1">
                             </div>

                             <!-- Source Type -->
                             <div>
                                 <label class="text-xs text-gray-400 mb-1 block">Source Type</label>
                                 <div class="flex gap-4">
                                     <label class="flex items-center gap-2 cursor-pointer">
                                         <input type="radio" name="ert_msg_source" value="manual" checked class="accent-[#d946ef]" onchange="updateERTMsgSourceUI()">
                                         <span class="text-sm text-gray-300">Manual</span>
                                     </label>
                                     <label class="flex items-center gap-2 cursor-pointer">
                                         <input type="radio" name="ert_msg_source" value="file" class="accent-[#d946ef]" onchange="updateERTMsgSourceUI()">
                                         <span class="text-sm text-gray-300">File</span>
                                     </label>
                                     <label class="flex items-center gap-2 cursor-pointer">
                                         <input type="radio" name="ert_msg_source" value="url" class="accent-[#d946ef]" onchange="updateERTMsgSourceUI()">
                                         <span class="text-sm text-gray-300">URL</span>
                                     </label>
                                 </div>
                             </div>

                             <!-- Manual Content -->
                             <div id="ert_msg_manual_content">
                                 <label class="text-xs text-gray-400 mb-1 block">eRT Text (UTF-8/UCS-2, max 128 bytes)</label>
                                 <textarea id="ert_msg_text" rows="3" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 font-mono text-sm" placeholder="Enter your enhanced RadioText message..." oninput="updateERTMsgPreview()"></textarea>
                                 <div class="text-xs text-gray-500 mt-1">Supports international characters, emojis, and extended character sets</div>
                             </div>

                             <!-- File/URL Content -->
                             <div id="ert_msg_external_content" style="display:none">
                                 <label class="text-xs text-gray-400 mb-1 block" id="ert_content_label">File Path</label>
                                 <input type="text" id="ert_msg_content" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1" placeholder="/path/to/file.txt" oninput="updateERTMsgPreview()">
                                 <div class="text-xs text-gray-500 mt-1" id="ert_content_hint">Path to text file containing eRT content</div>
                             </div>

                             <!-- Content Preview -->
                             <div class="bg-[#111] border border-[#444] rounded p-2">
                                 <div class="text-xs text-gray-400 mb-1">Content Preview:</div>
                                 <div id="ert_msg_preview" class="text-xs font-mono text-green-400">No content to preview</div>
                             </div>

                             <!-- Enable/Disable Toggle -->
                             <div class="flex items-center justify-between">
                                 <label class="text-sm text-gray-300">Message Enabled</label>
                                 <input type="checkbox" id="ert_msg_enabled" checked class="toggle-checkbox">
                             </div>

                             <!-- RT+ Configuration for eRT -->
                             <div class="bg-[#1a1a1a] border border-[#333] rounded p-3 space-y-3">
                                 <div class="flex justify-between items-center">
                                     <label class="text-xs text-orange-400 font-bold">eRT+ Tags</label>
                                     <label class="flex items-center gap-2 cursor-pointer">
                                         <span class="text-xs text-gray-400">Enable for this message</span>
                                         <input type="checkbox" id="ert_msg_rtplus_enabled" class="toggle-checkbox" onchange="updateERTRTPlusUI()">
                                     </label>
                                 </div>

                                 <!-- eRT+ Tagging Options -->
                                 <div id="ert_msg_rtplus_options" style="display:none" class="space-y-4">

                                     <!-- Sample text for manual mode -->
                                     <div id="ert_msg_rtplus_sample_wrap" style="display:none">
                                         <label class="text-xs text-gray-400 mb-1 block">Sample Text for Rule Testing</label>
                                         <input type="text" id="ert_msg_sample_text" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1" placeholder="e.g. Adele - Rolling in the Deep" oninput="updateERTSampleText(); updateERTRTPlusPreview()">
                                         <div class="text-[10px] text-gray-500 mt-1">💡 Enter sample text to preview how tagging rules apply</div>
                                     </div>

                                     <!-- Policy Manager -->
                                     <div class="bg-[#252525] border border-blue-600/50 rounded p-3">
                                         <div class="flex items-center justify-between mb-3">
                                             <div class="flex items-center gap-2">
                                                 <div class="text-blue-400 font-bold">🎯 Tagging Policies</div>
                                                 <div class="text-xs text-blue-300">Define how content gets tagged</div>
                                             </div>
                                             <button onclick="addERTTaggingPolicy()" class="px-3 py-1 bg-blue-800 hover:bg-blue-700 rounded text-xs text-white font-bold">+ Add Policy</button>
                                         </div>
                                         <div id="ert_tagging_policies_list" class="space-y-2"></div>
                                         <div class="text-[9px] text-gray-500 mt-3 border-t border-gray-700 pt-2">
                                             💡 <strong>Default</strong>: Base tagging • <strong>Sub-tagging</strong>: Conditional rules • Evaluated top-to-bottom
                                         </div>
                                     </div>

                                     <!-- Policy Editor -->
                                     <div id="ert_policy_editor" style="display:none" class="bg-[#1a1a1a] border border-purple-600/50 rounded p-4 space-y-3">
                                         <div class="flex items-center justify-between">
                                             <h3 class="text-purple-400 font-bold" id="ert_policy_editor_title">Edit Policy</h3>
                                             <button onclick="closeERTPolicyEditor()" class="text-gray-400 hover:text-white">✕</button>
                                         </div>
                                         <div class="grid grid-cols-2 gap-3">
                                             <div>
                                                 <label class="text-xs text-gray-400 block mb-1">Policy Name</label>
                                                 <input type="text" id="ert_policy_name" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" placeholder="e.g. Default Music">
                                             </div>
                                             <div>
                                                 <label class="text-xs text-gray-400 block mb-1">Policy Type</label>
                                                 <select id="ert_policy_type" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" onchange="updateERTPolicyEditor()">
                                                     <option value="default">Default</option>
                                                     <option value="sub">Sub-tagging</option>
                                                 </select>
                                             </div>
                                         </div>
                                         <!-- Default Policy Settings -->
                                         <div id="ert_default_policy_settings" class="space-y-3">
                                             <div class="text-xs text-green-400 font-bold">📍 Default Tagging Settings</div>
                                             <div class="grid grid-cols-2 gap-3">
                                                 <div>
                                                     <label class="text-xs text-orange-400">Tag 1 Type</label>
                                                     <select id="ert_policy_default_tag1" class="w-full bg-[#111] border border-orange-900/50 rounded px-2 py-1 text-xs"></select>
                                                 </div>
                                                 <div>
                                                     <label class="text-xs text-cyan-400">Tag 2 Type</label>
                                                     <select id="ert_policy_default_tag2" class="w-full bg-[#111] border border-cyan-900/50 rounded px-2 py-1 text-xs"></select>
                                                 </div>
                                             </div>
                                             <div class="grid grid-cols-3 gap-3">
                                                 <div>
                                                     <label class="text-xs text-gray-400">Split Pattern</label>
                                                     <input type="text" id="ert_policy_default_split" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs" value=" - ">
                                                 </div>
                                                 <div>
                                                     <label class="text-xs text-gray-400">Prefix</label>
                                                     <input type="text" id="ert_policy_default_prefix" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs" placeholder="Text before">
                                                 </div>
                                                 <div>
                                                     <label class="text-xs text-gray-400">Suffix</label>
                                                     <input type="text" id="ert_policy_default_suffix" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs" placeholder="Text after">
                                                 </div>
                                             </div>
                                         </div>
                                         <!-- Sub-tagging Policy Settings -->
                                         <div id="ert_sub_policy_settings" style="display:none" class="space-y-3">
                                             <div class="text-xs text-cyan-400 font-bold">🎯 Content Trigger (Optional)</div>
                                             <div class="bg-[#0a0a0a] p-3 rounded border border-cyan-900/30 space-y-2">
                                                 <div class="grid grid-cols-2 gap-3">
                                                     <div>
                                                         <label class="text-xs text-gray-400">Trigger Type</label>
                                                         <select id="ert_policy_sub_trigger_type" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs">
                                                             <option value="none">No Trigger (Always Apply)</option>
                                                             <option value="contains">Content contains text</option>
                                                             <option value="starts_with">Content starts with text</option>
                                                             <option value="ends_with">Content ends with text</option>
                                                             <option value="equals">Content exactly equals</option>
                                                             <option value="regex">Content matches regex</option>
                                                         </select>
                                                     </div>
                                                     <div>
                                                         <label class="text-xs text-gray-400">Trigger Pattern</label>
                                                         <input type="text" id="ert_policy_sub_trigger_pattern" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs" placeholder="e.g. Station Name">
                                                     </div>
                                                 </div>
                                             </div>
                                             <div class="text-xs text-purple-400 font-bold">⚡ Sub-tagging Condition</div>
                                             <div class="grid grid-cols-3 gap-3">
                                                 <div>
                                                     <label class="text-xs text-gray-400">Condition</label>
                                                     <select id="ert_policy_sub_condition" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs">
                                                         <option value="starts_with">Starts with</option>
                                                         <option value="ends_with">Ends with</option>
                                                         <option value="contains">Contains</option>
                                                         <option value="equals">Exactly equals</option>
                                                         <option value="regex">Regex pattern</option>
                                                     </select>
                                                 </div>
                                                 <div>
                                                     <label class="text-xs text-gray-400">Pattern</label>
                                                     <input type="text" id="ert_policy_sub_pattern" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs" placeholder="e.g. +++, Live:">
                                                 </div>
                                                 <div>
                                                     <label class="text-xs text-gray-400">Tag Action</label>
                                                     <select id="ert_policy_sub_action" class="w-full bg-[#111] border border-[#444] rounded px-2 py-1 text-xs">
                                                         <option value="tag_all">Tag entire content</option>
                                                         <option value="tag_after">Tag content after pattern</option>
                                                         <option value="tag_before">Tag content before pattern</option>
                                                         <option value="tag_match">Tag only the match</option>
                                                     </select>
                                                 </div>
                                             </div>
                                             <div class="grid grid-cols-2 gap-3">
                                                 <div>
                                                     <label class="text-xs text-orange-400">Tag Type</label>
                                                     <select id="ert_policy_sub_tag_type" class="w-full bg-[#111] border border-orange-900/50 rounded px-2 py-1 text-xs"></select>
                                                 </div>
                                                 <div class="flex items-center gap-2 mt-4">
                                                     <input type="checkbox" id="ert_policy_sub_strip_pattern" class="accent-purple-600">
                                                     <span class="text-xs text-gray-300">Remove pattern from tagged text</span>
                                                 </div>
                                             </div>
                                         </div>
                                         <div class="flex gap-2 pt-3 border-t border-gray-700">
                                             <button onclick="saveERTPolicyEditor()" class="px-4 py-2 bg-green-800 hover:bg-green-700 rounded text-sm text-white font-bold">Save Policy</button>
                                             <button onclick="closeERTPolicyEditor()" class="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded text-sm text-white">Cancel</button>
                                         </div>
                                     </div>

                                     <!-- Live Preview -->
                                     <div class="bg-[#000] border border-[#333] rounded p-3">
                                         <div class="flex justify-between items-center mb-2">
                                             <div class="flex items-center gap-2">
                                                 <label class="text-xs text-gray-400 font-bold">PREVIEW</label>
                                                 <span class="text-gray-500">-</span>
                                                 <span id="ert_msg_rule_applied" class="text-gray-400 text-xs">Current policy: –</span>
                                             </div>
                                             <div class="text-xs">
                                                 <span id="ert_msg_char_count" class="text-green-400">0</span>
                                                 <span class="text-gray-500">/128</span>
                                                 <span class="text-gray-600 ml-1">bytes</span>
                                             </div>
                                         </div>
                                         <div id="ert_rtplus_preview" class="font-mono text-sm text-gray-300 min-h-[20px] whitespace-pre-wrap break-words"></div>
                                         <div class="mt-2 grid grid-cols-2 gap-2 text-xs">
                                             <div class="text-orange-400">Tag 1: <span id="ert_msg_tag1_info" class="text-orange-300">-</span></div>
                                             <div class="text-cyan-400">Tag 2: <span id="ert_msg_tag2_info" class="text-cyan-300">-</span></div>
                                         </div>
                                     </div>

                                 </div>
                             </div>

                             <!-- Text Processing / Trim Options -->
                             <div class="bg-[#1a1a1a] border border-[#333] rounded p-3 space-y-2">
                                 <label class="text-xs text-teal-400 font-bold block">✂️ Text Processing</label>
                                 <div class="grid grid-cols-2 gap-2 text-xs">
                                     <label class="flex items-center gap-2 cursor-pointer">
                                         <input type="checkbox" id="ert_msg_trim_parens" class="accent-teal-600">
                                         <span class="text-gray-300">Remove (...) parentheses</span>
                                     </label>
                                     <label class="flex items-center gap-2 cursor-pointer">
                                         <input type="checkbox" id="ert_msg_trim_brackets" class="accent-teal-600">
                                         <span class="text-gray-300">Remove [...] brackets</span>
                                     </label>
                                     <label class="flex items-center gap-2 cursor-pointer">
                                         <input type="checkbox" id="ert_msg_trim_semicolon" class="accent-teal-600">
                                         <span class="text-gray-300">Trim at ; per segment</span>
                                     </label>
                                     <label class="flex items-center gap-2 cursor-pointer">
                                         <input type="checkbox" id="ert_msg_trim_maxlen" class="accent-teal-600">
                                         <span class="text-gray-300">Truncate to 128 bytes</span>
                                     </label>
                                 </div>
                             </div>
                         </div>

                         <div class="rtplus-modal-footer">
                             <button onclick="closeERTMsgModal()" class="px-4 py-2 bg-[#444] hover:bg-[#555] rounded text-sm">Cancel</button>
                             <button onclick="saveERTMessage()" class="px-4 py-2 bg-[#d946ef] hover:bg-[#c026d3] rounded text-sm text-white font-bold">Save</button>
                         </div>
                     </div>
                 </div>

                 <div class="section">
                    <div class="section-header">Programme Item Number (PIN) - Group 1A</div>
                    <div class="section-body">
                         <div class="flex items-start gap-2 mb-2">
                             <div class="flex-1">
                                 <label>Enable PIN</label>
                                 <div class="text-[9px] text-gray-500">Broadcast programme start time (day, hour, minute)</div>
                             </div>
                             <input type="checkbox" class="toggle-checkbox" id="en_pin" {% if state.en_pin %}checked{% endif %} onchange="sync()">
                         </div>
                         <div class="grid grid-cols-3 gap-2">
                             <div>
                                 <label>Day (1-31, 0=invalid)</label>
                                 <select id="pin_day" onchange="sync()">
                                     <option value="0" {% if state.pin_day == 0 %}selected{% endif %}>0 (Invalid)</option>
                                     {% for d in range(1, 32) %}
                                     <option value="{{d}}" {% if state.pin_day == d %}selected{% endif %}>{{d}}</option>
                                     {% endfor %}
                                 </select>
                             </div>
                             <div>
                                 <label>Hour (0-23)</label>
                                 <select id="pin_hour" onchange="sync()">
                                     {% for h in range(0, 24) %}
                                     <option value="{{h}}" {% if state.pin_hour == h %}selected{% endif %}>{{"%02d"|format(h)}}</option>
                                     {% endfor %}
                                 </select>
                             </div>
                             <div>
                                 <label>Minute (0-59)</label>
                                 <select id="pin_minute" onchange="sync()">
                                     {% for m in range(0, 60) %}
                                     <option value="{{m}}" {% if state.pin_minute == m %}selected{% endif %}>{{"%02d"|format(m)}}</option>
                                     {% endfor %}
                                 </select>
                             </div>
                         </div>
                    </div>
                 </div>

                 <div class="section">
                    <div class="section-header">Transparent Data Channels (TDC) - Groups 5A/5B</div>
                    <div class="section-body">
                         <div class="text-[9px] text-gray-500 mb-3">
                             Send custom data or PC status info on Type 5A/5B groups. Channel numbers identify different data streams (0-31).
                             Supports external sources: \w"url" for web content, \r"file" for files.
                         </div>

                         <!-- Type 5A Configuration -->
                         <div class="mb-4 p-2 border border-gray-700 rounded">
                             <div class="flex items-start gap-2 mb-2">
                                 <div class="flex-1">
                                     <label class="font-semibold text-blue-300">Type 5A (Full Data)</label>
                                     <div class="text-[9px] text-gray-500">Up to 128 bytes with CR terminator (32 segments × 4 bytes)</div>
                                 </div>
                                 <input type="checkbox" class="toggle-checkbox" id="en_tdc_5a" {% if state.en_tdc_5a %}checked{% endif %} onchange="sync()">
                             </div>
                             <div class="grid grid-cols-2 gap-2 mb-2">
                                 <div>
                                     <label>Channel Number (0-31)</label>
                                     <input type="number" id="tdc_5a_channel" min="0" max="31" value="{{state.tdc_5a_channel}}" onchange="sync()">
                                 </div>
                                 <div>
                                     <label>Mode</label>
                                     <select id="tdc_5a_mode" onchange="sync(); toggleTdcMode('5a')">
                                         <option value="custom" {% if state.tdc_5a_mode == 'custom' %}selected{% endif %}>Custom Text</option>
                                         <option value="pc_status" {% if state.tdc_5a_mode == 'pc_status' %}selected{% endif %}>PC Status</option>
                                     </select>
                                 </div>
                             </div>
                             <div id="tdc_5a_text_container" {% if state.tdc_5a_mode == 'pc_status' %}style="display:none"{% endif %}>
                                 <label>Custom Text (up to 128 chars, CR-terminated, supports \w"url" and \r"file")</label>
                                 <input type="text" id="tdc_5a_text" maxlength="128" value="{{state.tdc_5a_text}}" onchange="sync()">
                             </div>
                         </div>

                         <!-- Type 5B Configuration -->
                         <div class="mb-4 p-2 border border-gray-700 rounded">
                             <div class="flex items-start gap-2 mb-2">
                                 <div class="flex-1">
                                     <label class="font-semibold text-blue-300">Type 5B (Full Data)</label>
                                     <div class="text-[9px] text-gray-500">Up to 128 bytes with CR terminator (32 segments × 4 bytes)</div>
                                 </div>
                                 <input type="checkbox" class="toggle-checkbox" id="en_tdc_5b" {% if state.en_tdc_5b %}checked{% endif %} onchange="sync()">
                             </div>
                             <div class="grid grid-cols-2 gap-2 mb-2">
                                 <div>
                                     <label>Channel Number (0-31)</label>
                                     <input type="number" id="tdc_5b_channel" min="0" max="31" value="{{state.tdc_5b_channel}}" onchange="sync()">
                                 </div>
                                 <div>
                                     <label>Mode</label>
                                     <select id="tdc_5b_mode" onchange="sync(); toggleTdcMode('5b')">
                                         <option value="custom" {% if state.tdc_5b_mode == 'custom' %}selected{% endif %}>Custom Text</option>
                                         <option value="pc_status" {% if state.tdc_5b_mode == 'pc_status' %}selected{% endif %}>PC Status</option>
                                     </select>
                                 </div>
                             </div>
                             <div id="tdc_5b_text_container" {% if state.tdc_5b_mode == 'pc_status' %}style="display:none"{% endif %}>
                                 <label>Custom Text (up to 128 chars, CR-terminated, supports \w"url" and \r"file")</label>
                                 <input type="text" id="tdc_5b_text" maxlength="128" value="{{state.tdc_5b_text}}" onchange="sync()">
                             </div>
                         </div>

                         <!-- PC Status Options (shown when either mode is pc_status) -->
                         <div id="tdc_pc_options" {% if state.tdc_5a_mode != 'pc_status' and state.tdc_5b_mode != 'pc_status' %}style="display:none"{% endif %} class="p-2 border border-blue-700 rounded bg-blue-950 bg-opacity-20">
                             <label class="font-semibold text-blue-300 mb-2 block">PC Status Display Options</label>
                             <div class="text-[9px] text-gray-400 mb-2">Format: RDS-MASTER v1.2 | CPU: XX% | Temp: XXC | RAM: XX% | IP: XXX.XXX | Uptime: Xd Xh Xm</div>
                             <div class="grid grid-cols-3 gap-2 text-sm">
                                 <label class="flex items-center gap-2">
                                     <input type="checkbox" class="toggle-checkbox" id="tdc_pc_show_cpu" {% if state.tdc_pc_show_cpu %}checked{% endif %} onchange="sync()">
                                     <span>Show CPU</span>
                                 </label>
                                 <label class="flex items-center gap-2">
                                     <input type="checkbox" class="toggle-checkbox" id="tdc_pc_show_temp" {% if state.tdc_pc_show_temp %}checked{% endif %} onchange="sync()">
                                     <span>Show Temp</span>
                                 </label>
                                 <label class="flex items-center gap-2">
                                     <input type="checkbox" class="toggle-checkbox" id="tdc_pc_show_ip" {% if state.tdc_pc_show_ip %}checked{% endif %} onchange="sync()">
                                     <span>Show IP</span>
                                 </label>
                                 <label class="flex items-center gap-2">
                                     <input type="checkbox" class="toggle-checkbox" id="tdc_pc_show_ram" {% if state.tdc_pc_show_ram %}checked{% endif %} onchange="sync()">
                                     <span>Show RAM</span>
                                 </label>
                                 <label class="flex items-center gap-2">
                                     <input type="checkbox" class="toggle-checkbox" id="tdc_pc_show_uptime" {% if state.tdc_pc_show_uptime %}checked{% endif %} onchange="sync()">
                                     <span>Show Uptime</span>
                                 </label>
                             </div>
                         </div>
                    </div>
                 </div>

                 <div class="section">
                    <div class="section-header">DAB Cross-Reference (Group 12A)</div>
                    <div class="section-body">
                         <div class="flex items-start gap-2 mb-2">
                             <div class="flex-1">
                                 <label>Enable DAB Linkage</label>
                                 <div class="text-[9px] text-gray-500">Broadcast associated DAB ensemble frequency</div>
                                 <div class="text-[9px] text-amber-600 font-semibold">⚠️ EXPERIMENTAL: Not fully tested</div>
                             </div>
                             <input type="checkbox" class="toggle-checkbox" id="en_dab" {% if state.en_dab %}checked{% endif %} onchange="sync()">
                         </div>
                         <div class="mb-3">
                             <label>DAB Channel</label>
                             <select id="dab_channel" onchange="sync()">
                                 <option value="5A" {% if state.dab_channel == '5A' %}selected{% endif %}>5A (174.928 MHz)</option>
                                 <option value="5B" {% if state.dab_channel == '5B' %}selected{% endif %}>5B (176.640 MHz)</option>
                                 <option value="5C" {% if state.dab_channel == '5C' %}selected{% endif %}>5C (178.352 MHz)</option>
                                 <option value="5D" {% if state.dab_channel == '5D' %}selected{% endif %}>5D (180.064 MHz)</option>
                                 <option value="6A" {% if state.dab_channel == '6A' %}selected{% endif %}>6A (181.936 MHz)</option>
                                 <option value="6B" {% if state.dab_channel == '6B' %}selected{% endif %}>6B (183.648 MHz)</option>
                                 <option value="6C" {% if state.dab_channel == '6C' %}selected{% endif %}>6C (185.360 MHz)</option>
                                 <option value="6D" {% if state.dab_channel == '6D' %}selected{% endif %}>6D (187.072 MHz)</option>
                                 <option value="7A" {% if state.dab_channel == '7A' %}selected{% endif %}>7A (188.928 MHz)</option>
                                 <option value="7B" {% if state.dab_channel == '7B' %}selected{% endif %}>7B (190.640 MHz)</option>
                                 <option value="7C" {% if state.dab_channel == '7C' %}selected{% endif %}>7C (192.352 MHz)</option>
                                 <option value="7D" {% if state.dab_channel == '7D' %}selected{% endif %}>7D (194.064 MHz)</option>
                                 <option value="8A" {% if state.dab_channel == '8A' %}selected{% endif %}>8A (195.936 MHz)</option>
                                 <option value="8B" {% if state.dab_channel == '8B' %}selected{% endif %}>8B (197.648 MHz)</option>
                                 <option value="8C" {% if state.dab_channel == '8C' %}selected{% endif %}>8C (199.360 MHz)</option>
                                 <option value="8D" {% if state.dab_channel == '8D' %}selected{% endif %}>8D (201.072 MHz)</option>
                                 <option value="9A" {% if state.dab_channel == '9A' %}selected{% endif %}>9A (202.928 MHz)</option>
                                 <option value="9B" {% if state.dab_channel == '9B' %}selected{% endif %}>9B (204.640 MHz)</option>
                                 <option value="9C" {% if state.dab_channel == '9C' %}selected{% endif %}>9C (206.352 MHz)</option>
                                 <option value="9D" {% if state.dab_channel == '9D' %}selected{% endif %}>9D (208.064 MHz)</option>
                                 <option value="10A" {% if state.dab_channel == '10A' %}selected{% endif %}>10A (209.936 MHz)</option>
                                 <option value="10B" {% if state.dab_channel == '10B' %}selected{% endif %}>10B (211.648 MHz)</option>
                                 <option value="10C" {% if state.dab_channel == '10C' %}selected{% endif %}>10C (213.360 MHz)</option>
                                 <option value="10D" {% if state.dab_channel == '10D' %}selected{% endif %}>10D (215.072 MHz)</option>
                                 <option value="10N" {% if state.dab_channel == '10N' %}selected{% endif %}>10N (210.096 MHz)</option>
                                 <option value="11A" {% if state.dab_channel == '11A' %}selected{% endif %}>11A (216.928 MHz)</option>
                                 <option value="11B" {% if state.dab_channel == '11B' %}selected{% endif %}>11B (218.640 MHz)</option>
                                 <option value="11C" {% if state.dab_channel == '11C' %}selected{% endif %}>11C (220.352 MHz)</option>
                                 <option value="11D" {% if state.dab_channel == '11D' %}selected{% endif %}>11D (222.064 MHz)</option>
                                 <option value="11N" {% if state.dab_channel == '11N' %}selected{% endif %}>11N (217.088 MHz)</option>
                                 <option value="12A" {% if state.dab_channel == '12A' %}selected{% endif %}>12A (223.936 MHz)</option>
                                 <option value="12B" {% if state.dab_channel == '12B' %}selected{% endif %}>12B (225.648 MHz)</option>
                                 <option value="12C" {% if state.dab_channel == '12C' %}selected{% endif %}>12C (227.360 MHz)</option>
                                 <option value="12D" {% if state.dab_channel == '12D' %}selected{% endif %}>12D (229.072 MHz)</option>
                                 <option value="12N" {% if state.dab_channel == '12N' %}selected{% endif %}>12N (224.096 MHz)</option>
                                 <option value="13A" {% if state.dab_channel == '13A' %}selected{% endif %}>13A (230.784 MHz)</option>
                                 <option value="13B" {% if state.dab_channel == '13B' %}selected{% endif %}>13B (232.496 MHz)</option>
                                 <option value="13C" {% if state.dab_channel == '13C' %}selected{% endif %}>13C (234.208 MHz)</option>
                                 <option value="13D" {% if state.dab_channel == '13D' %}selected{% endif %}>13D (235.776 MHz)</option>
                                 <option value="13E" {% if state.dab_channel == '13E' %}selected{% endif %}>13E (237.488 MHz)</option>
                                 <option value="13F" {% if state.dab_channel == '13F' %}selected{% endif %}>13F (239.200 MHz)</option>
                             </select>
                         </div>
                         
                         <div class="grid grid-cols-2 gap-3 mb-3">
                             <div>
                                 <label>Ensemble ID (EId)</label>
                                 <div class="text-[9px] text-gray-500 mb-1">16-bit hex (e.g., CE15)</div>
                                 <input type="text" id="dab_eid" value="{{ state.dab_eid }}" maxlength="4" pattern="[0-9A-Fa-f]{4}" class="w-full px-2 py-1 bg-gray-800 border border-gray-600 rounded" onchange="sync()">
                             </div>
                             <div>
                                 <label>DAB Mode</label>
                                 <div class="text-[9px] text-gray-500 mb-1">Transmission mode</div>
                                 <select id="dab_mode" onchange="sync()" class="w-full">
                                     <option value="0" {% if state.dab_mode == 0 %}selected{% endif %}>Unspecified</option>
                                     <option value="1" {% if state.dab_mode == 1 %}selected{% endif %}>Mode I</option>
                                     <option value="2" {% if state.dab_mode == 2 %}selected{% endif %}>Mode II/III</option>
                                     <option value="3" {% if state.dab_mode == 3 %}selected{% endif %}>Mode IV</option>
                                 </select>
                             </div>
                         </div>
                         
                         <div class="grid grid-cols-2 gap-3 mb-3">
                             <div>
                                 <label>Table Type (E/S Flag)</label>
                                 <div class="text-[9px] text-gray-500 mb-1">Ensemble or Service table</div>
                                 <select id="dab_es_flag" onchange="sync()" class="w-full">
                                     <option value="0" {% if state.dab_es_flag == 0 %}selected{% endif %}>Ensemble (0)</option>
                                     <option value="1" {% if state.dab_es_flag == 1 %}selected{% endif %}>Service (1)</option>
                                 </select>
                             </div>
                             <div>
                                 <label>Service ID (SId)</label>
                                 <div class="text-[9px] text-gray-500 mb-1">16-bit hex (for service table)</div>
                                 <input type="text" id="dab_sid" value="{{ state.dab_sid }}" maxlength="4" pattern="[0-9A-Fa-f]{4}" class="w-full px-2 py-1 bg-gray-800 border border-gray-600 rounded" onchange="sync()">
                             </div>
                         </div>
                         
                         <div>
                             <label>Variant Code</label>
                             <div class="text-[9px] text-gray-500 mb-1">For service table format</div>
                             <select id="dab_variant" onchange="sync()" class="w-full">
                                 <option value="0" {% if state.dab_variant == 0 %}selected{% endif %}>0 - Ensemble information</option>
                                 <option value="1" {% if state.dab_variant == 1 %}selected{% endif %}>1 - Linkage information</option>
                             </select>
                         </div>
                    </div>
                 </div>

                 <div class="section">
                    <div class="section-header">Custom ODA Flags (Group 3A)</div>
                    <div class="section-body">
                         <div class="flex justify-between items-center mb-3">
                             <div>
                                 <label>Custom Open Data Applications</label>
                                 <div class="text-[9px] text-gray-500">Add custom ODA announcements alongside DAB, RT+, and RDS2</div>
                                 <div class="text-[9px] text-amber-600 font-semibold">⚠️ EXPERIMENTAL: Use registered AID codes only</div>
                             </div>
                         </div>

                         <input type="hidden" id="custom_oda_list" value="{{state.custom_oda_list}}">

                         <div class="mb-3">
                             <button onclick="openCustomODAModal()" class="bg-indigo-600 hover:bg-indigo-500 text-white rounded px-3 py-2 text-sm w-full">Manage Custom ODAs</button>
                         </div>

                         <div id="custom_oda_display" class="text-xs text-gray-400">
                             No custom ODAs configured
                         </div>
                    </div>
                 </div>

                 <div class="section">
                    <div class="section-header">EON - Enhanced Other Networks (Group 14A)</div>
                    <div class="section-body">
                         <div class="flex items-start gap-2 mb-3">
                             <div class="flex-1">
                                 <label>Enable EON</label>
                                 <div class="text-[9px] text-gray-500">Broadcast information about other radio stations</div>
                             </div>
                             <input type="checkbox" class="toggle-checkbox" id="en_eon" {% if state.en_eon %}checked{% endif %} onchange="sync()">
                         </div>

                         <input type="hidden" id="eon_services" value="{{state.eon_services}}">

                         <div class="mb-3">
                             <button onclick="openEONModal()" class="bg-blue-600 hover:bg-blue-500 text-white rounded px-3 py-2 text-sm w-full">Manage EON Services</button>
                         </div>

                         <div id="eon_services_display" class="text-xs text-gray-400">
                             No services configured
                         </div>
                    </div>
                 </div>

                 <div class="section">
                    <div class="section-header">Custom Data Groups</div>
                    <div class="section-body">
                         <div class="flex justify-between items-center mb-3">
                             <div>
                                 <label>Custom RDS Group Data</label>
                                 <div class="text-[9px] text-gray-500">Define custom data for any group type (0A-15B)</div>
                                 <div class="text-[9px] text-amber-600 font-semibold">⚠️ ADVANCED: For expert users only</div>
                             </div>
                         </div>

                         <input type="hidden" id="custom_groups" value="{{state.custom_groups}}">

                         <div class="mb-3">
                             <button onclick="openCustomGroupsModal()" class="bg-purple-600 hover:bg-purple-500 text-white rounded px-3 py-2 text-sm w-full">Manage Custom Groups</button>
                         </div>

                         <div id="custom_groups_display" class="text-xs text-gray-400">
                             No custom groups configured
                         </div>
                    </div>
                 </div>

                 <div class="section">
                    <div class="section-header">Basic Dynamic Control</div>
                    <div class="section-body">
                         <div class="flex items-start gap-2 mb-3">
                             <div class="flex-1">
                                 <label>Dynamic RDS Parameter Control</label>
                                 <div class="text-[9px] text-gray-500">Automatically control RDS parameters (PTY, MS, TP, TA, PI, PTYN, PIN, TDC) from JSON data</div>
                                 <div class="text-[9px] text-amber-600 font-semibold">⚠️ Changes take effect immediately</div>
                             </div>
                             <input type="checkbox" class="toggle-checkbox" id="dynamic_control_enabled" {% if state.dynamic_control_enabled %}checked{% endif %} onchange="sync()">
                         </div>

                         <input type="hidden" id="dynamic_control_rules" value="{{state.dynamic_control_rules}}">

                         <div class="mb-3">
                             <button onclick="openDynamicControlModal()" class="bg-cyan-600 hover:bg-cyan-500 text-white rounded px-3 py-2 text-sm w-full">Manage Control Rules</button>
                         </div>

                         <div id="dynamic_control_rules_display" class="text-xs text-gray-400">
                             No control rules configured
                         </div>
                    </div>
                 </div>

                 <div class="section">
                    <div class="section-header">ARI (Autofahrer-Rundfunk-Information)</div>
                    <div class="section-body">
                         <div class="flex items-start gap-2 mb-3">
                             <div class="flex-1">
                                 <label>Enable ARI Traffic Radio</label>
                                 <div class="text-[9px] text-gray-500">German/European traffic information system - separate 57 kHz carrier with AM</div>
                                 <div class="text-[9px] text-cyan-400">RDS biphase has spectral notch at 57 kHz center, allowing ARI carrier to coexist</div>
                             </div>
                             <input type="checkbox" class="toggle-checkbox" id="en_ari" {% if state.en_ari %}checked{% endif %} onchange="sync()">
                         </div>

                         <div class="grid-cols-2">
                             <div>
                                 <label>Region Identifier (BK)</label>
                                 <select id="ari_region" onchange="sync()">
                                     <option value="A" {% if state.ari_region == "A" %}selected{% endif %}>Region A (23.75 Hz) - Mecklenburg-WP, Bremen, Sachsen, BW (US zone)</option>
                                     <option value="B" {% if state.ari_region == "B" %}selected{% endif %}>Region B (28.274 Hz) - Schleswig-Holstein, Sachsen-Anhalt, Saarland</option>
                                     <option value="C" {% if state.ari_region == "C" %}selected{% endif %}>Region C (34.926 Hz) - Hamburg, Berlin, NRW, Bayern Nord</option>
                                     <option value="D" {% if state.ari_region == "D" %}selected{% endif %}>Region D (39.583 Hz) - Niedersachsen SE, Rheinland-Pfalz, Bayern Süd</option>
                                     <option value="E" {% if state.ari_region == "E" %}selected{% endif %}>Region E (45.673 Hz) - Niedersachsen West, Thüringen, BW Süd (FR zone)</option>
                                     <option value="F" {% if state.ari_region == "F" %}selected{% endif %}>Region F (53.977 Hz) - Brandenburg, Hessen</option>
                                 </select>
                                 <div class="text-[9px] text-gray-500 mt-1">BK frequency determines transmission range identifier</div>
                             </div>

                             <div>
                                 <label>Announcement Mode (DK)</label>
                                 <select id="ari_announcement_mode" onchange="sync(); updateARIAnnouncementUI()">
                                     <option value="manual" {% if state.ari_announcement_mode == "manual" %}selected{% endif %}>Manual Control</option>
                                     <option value="rds_ta" {% if state.ari_announcement_mode == "rds_ta" %}selected{% endif %}>Auto (follows RDS TA+TP)</option>
                                 </select>
                                 <div class="text-[9px] text-gray-500 mt-1">DK = 125 Hz modulation for traffic announcements</div>
                             </div>
                         </div>

                         <div id="ari_manual_announcement" class="mt-3" style="display: {% if state.ari_announcement_mode == 'manual' %}block{% else %}none{% endif %}">
                             <div class="flex justify-between items-center bg-[#111] p-3 rounded">
                                 <div>
                                     <label class="font-semibold text-yellow-400">Traffic Announcement Active (DK)</label>
                                     <div class="text-[9px] text-gray-500">Manually trigger announcement identifier</div>
                                 </div>
                                 <input type="checkbox" class="toggle-checkbox" id="ari_announcement" {% if state.ari_announcement %}checked{% endif %} onchange="sync()">
                             </div>
                         </div>

                         <div id="ari_auto_announcement" class="mt-3" style="display: {% if state.ari_announcement_mode == 'rds_ta' %}block{% else %}none{% endif %}">
                             <div class="bg-[#0a1a0a] border border-green-900/50 rounded p-3">
                                 <div class="text-xs text-green-400 font-semibold mb-1">🔗 Announcement Auto Mode Active</div>
                                 <div class="text-[10px] text-gray-400">DK announcement will activate when both TP=1 and TA=1 in RDS flags</div>
                             </div>
                         </div>

                         <div class="mt-3 grid-cols-2">
                             <div>
                                 <label>BK Modulation Depth (%)</label>
                                 <input type="range" id="ari_bk_level" min="0" max="100" step="5" value="{{state.ari_bk_level}}" oninput="sync()">
                                 <span class="slider-val" id="val_ari_bk">{{state.ari_bk_level}}</span>
                                 <div class="text-[9px] text-gray-500">Standard: 60%</div>
                             </div>
                             <div>
                                 <label>DK Modulation Depth (%)</label>
                                 <input type="range" id="ari_dk_level" min="0" max="100" step="5" value="{{state.ari_dk_level}}" oninput="sync()">
                                 <span class="slider-val" id="val_ari_dk">{{state.ari_dk_level}}</span>
                                 <div class="text-[9px] text-gray-500">Standard: 30%</div>
                             </div>
                         </div>

                         <div class="mt-3 bg-[#1a1a0a] border border-amber-900/30 rounded p-3">
                             <div class="text-[10px] text-amber-300 font-semibold mb-2">ℹ️ ARI Technical Info</div>
                             <ul class="text-[9px] text-gray-400 space-y-1">
                                 <li>• <strong>SK</strong> (Sender Kennung): Separate 57 kHz pure carrier indicates traffic radio transmitter</li>
                                 <li>• <strong>BK</strong> (Bereichs Kennung): AM of 57 kHz carrier at region frequency (23.75-53.977 Hz, 60% depth)</li>
                                 <li>• <strong>DK</strong> (Durchsage Kennung): Additional 125 Hz AM (30% depth) for traffic announcements</li>
                                 <li>• <strong>Backwards compatible with RDS:</strong> RDS biphase has spectral null at 57 kHz, allowing ARI carrier</li>
                                 <li>• RDS (phase modulation) and ARI (AM) are separate 57 kHz signals, added together in MPX</li>
                                 <li>• Both carriers phase-locked to 19 kHz pilot with π shift to minimize peak (57 kHz = 3 × 19 kHz + π)</li>
                             </ul>
                         </div>
                    </div>
                 </div>
            </div>

            <div id="audio" class="content">
                <div class="section border-l-4 border-l-orange-500">
                    <div class="section-header text-orange-400">Audio I/O & Genlock</div>
                    <div class="section-body">
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label>Input Device (MPX or Audio)</label>
                                <select id="dev_in">
                                    <option value="-1">None (Internal Generator Only)</option>
                                    {% for d in inputs %}<option value="{{d.index}}" {% if d.index == state.device_in_idx %}selected{% endif %}>{{d.name}}</option>{% endfor %}
                                </select>
                            </div>
                            <div>
                                <label>Output Device (Transmitter)</label>
                                <select id="dev_out">
                                    {% for d in outputs %}<option value="{{d.index}}" {% if d.index == state.device_out_idx %}selected{% endif %}>{{d.name}}</option>{% endfor %}
                                </select>
                            </div>
                        </div>

                        <div class="mt-2">
                            <label>Output Channel</label>
                            <select id="output_channel" onchange="sync()">
                                <option value="both" {% if state.output_channel == "both" %}selected{% endif %}>Both (Stereo)</option>
                                <option value="left" {% if state.output_channel == "left" %}selected{% endif %}>Left Only</option>
                                <option value="right" {% if state.output_channel == "right" %}selected{% endif %}>Right Only</option>
                            </select>
                            <div class="text-[9px] text-gray-500 mt-1">Route RDS signal to specific channel(s)</div>
                        </div>

                        <div class="grid grid-cols-2 gap-4 mt-2">
                            <div class="bg-[#111] p-2 rounded flex justify-between items-center">
                                <div>
                                    <label class="text-orange-400">Pass-through</label>
                                    <div class="text-[9px] text-gray-500">Mix input audio to output</div>
                                </div>
                                <input type="checkbox" class="toggle-checkbox" id="passthrough" {% if state.passthrough %}checked{% endif %} onchange="sync()">
                            </div>
                            <div class="bg-[#111] p-2 rounded flex justify-between items-center">
                                <div>
                                    <label class="text-orange-400">Genlock (FFT Sync)</label>
                                    <div class="text-[9px] text-gray-500">Auto-lock RDS to Input 19k</div>
                                </div>
                                <input type="checkbox" class="toggle-checkbox" id="genlock" {% if state.genlock %}checked{% endif %} onchange="sync()">
                            </div>
                        </div>
                        
                        <div class="mt-2">
                            <label>Genlock Phase Offset (Degrees)</label>
                            <div class="slider-container">
                                <input type="range" id="genlock_offset" min="0" max="360" value="{{state.genlock_offset}}" oninput="sync()">
                                <span class="slider-val" id="val_offset">{{state.genlock_offset}}</span>
                            </div>
                        </div>

                        <div class="mt-2">
                            <label>RDS Carrier Frequency (Hz)</label>
                            <div class="grid grid-cols-2 gap-4 items-center">
                                <input type="number" id="rds_freq" value="{{state.rds_freq}}" min="1000" max="120000" step="1" class="w-32" onchange="sync()">
                                <div class="text-[11px] {{ 'text-red-400' if state.rds_freq != 57000 else 'text-gray-400' }}">
                                    ⚠ Non-compliant if not 57000 Hz. Use at your own risk.
                                </div>
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-4 mt-2">
                            <div>
                                <label>19kHz Pilot Level (%)</label>
                                <div class="slider-container">
                                    <input type="range" id="pilot_level" min="0" max="20" step="0.1" value="{{state.pilot_level}}" oninput="sync()">
                                    <span class="slider-val" id="val_pilot">{{state.pilot_level}}</span>
                                </div>
                                <div class="text-[11px] text-gray-400" id="pilot_note">Pilot will be disabled when pass-through is active with an input device</div>
                                <div class="text-[11px] text-gray-300 mt-1">Status: <span id="pilot_status">Unknown</span></div>
                            </div>
                            <div>
                                <label>RDS Carrier Level (%)</label>
                                <div class="slider-container">
                                    <input type="range" id="rds_level" min="0" max="20" step="0.1" value="{{state.rds_level}}" oninput="sync()">
                                    <span class="slider-val" id="val_rds">{{state.rds_level}}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div id="rds2" class="content">
                <div class="section border-l-4 border-l-purple-500">
                    <div class="section-header text-purple-400">RDS2 Multi-Carrier Configuration</div>
                    <div class="section-body">
                        <div class="bg-blue-900/20 border border-blue-500/30 rounded p-3 mb-4">
                            <div class="text-sm text-blue-300 font-semibold mb-1">ℹ️ About RDS2</div>
                            <div class="text-xs text-gray-300">
                                RDS2 adds up to 3 additional data carriers at 66.5, 71.25, and 76 kHz for enhanced data transmission.
                                These carriers use quadrature phase shifting to minimize peak amplitude.
                                <span class="text-yellow-400">⚠ Experimental feature</span> - verify with spectrum analyzer.
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-4 mb-4">
                            <div class="bg-[#111] p-3 rounded flex items-center gap-2">
                                <div class="flex-1">
                                    <label class="text-purple-400">Enable RDS2</label>
                                    <div class="text-[9px] text-gray-500">Activate additional RDS carriers</div>
                                </div>
                                <label class="switch"><input type="checkbox" id="en_rds2" onchange="sync()" {% if state.en_rds2 %}checked{% endif %}><span class="slider"></span></label>
                            </div>
                            <div>
                                <label>Number of RDS2 Carriers</label>
                                <select id="rds2_num_carriers" onchange="updateRDS2Visibility(); sync();">
                                    <option value="0" {% if state.rds2_num_carriers == 0 %}selected{% endif %}>0 (Disabled)</option>
                                    <option value="1" {% if state.rds2_num_carriers == 1 %}selected{% endif %}>1 Carrier (66.5 kHz)</option>
                                    <option value="2" {% if state.rds2_num_carriers == 2 %}selected{% endif %}>2 Carriers (66.5, 71.25 kHz)</option>
                                    <option value="3" {% if state.rds2_num_carriers == 3 %}selected{% endif %}>3 Carriers (66.5, 71.25, 76 kHz)</option>
                                </select>
                            </div>
                        </div>

                        <div class="grid grid-cols-1 gap-3">
                            <div class="rds2-carrier" id="rds2_carrier1_div">
                                <div class="bg-purple-900/20 border border-purple-500/30 rounded p-3">
                                    <div class="flex items-center justify-between mb-2">
                                        <label class="text-purple-300 font-semibold">Carrier 1 - 66.5 kHz</label>
                                        <span class="text-xs text-gray-400">Phase: 90°</span>
                                    </div>
                                    <div class="slider-container">
                                        <label class="text-xs">Modulation Level (%)</label>
                                        <input type="range" id="rds2_carrier1_level" min="0" max="15" step="0.1" value="{{state.rds2_carrier1_level}}" oninput="sync()">
                                        <span class="slider-val" id="val_rds2_c1">{{state.rds2_carrier1_level}}</span>
                                        <div class="text-[9px] text-gray-500 mt-1">Recommended: 4.5% (max 15%)</div>
                                    </div>
                                </div>
                            </div>

                            <div class="rds2-carrier" id="rds2_carrier2_div">
                                <div class="bg-purple-900/20 border border-purple-500/30 rounded p-3">
                                    <div class="flex items-center justify-between mb-2">
                                        <label class="text-purple-300 font-semibold">Carrier 2 - 71.25 kHz</label>
                                        <span class="text-xs text-gray-400">Phase: 180°</span>
                                    </div>
                                    <div class="slider-container">
                                        <label class="text-xs">Modulation Level (%)</label>
                                        <input type="range" id="rds2_carrier2_level" min="0" max="15" step="0.1" value="{{state.rds2_carrier2_level}}" oninput="sync()">
                                        <span class="slider-val" id="val_rds2_c2">{{state.rds2_carrier2_level}}</span>
                                        <div class="text-[9px] text-gray-500 mt-1">Recommended: 4.5% (max 15%)</div>
                                    </div>
                                </div>
                            </div>

                            <div class="rds2-carrier" id="rds2_carrier3_div">
                                <div class="bg-purple-900/20 border border-purple-500/30 rounded p-3">
                                    <div class="flex items-center justify-between mb-2">
                                        <label class="text-purple-300 font-semibold">Carrier 3 - 76 kHz</label>
                                        <span class="text-xs text-gray-400">Phase: 270°</span>
                                    </div>
                                    <div class="slider-container">
                                        <label class="text-xs">Modulation Level (%)</label>
                                        <input type="range" id="rds2_carrier3_level" min="0" max="15" step="0.1" value="{{state.rds2_carrier3_level}}" oninput="sync()">
                                        <span class="slider-val" id="val_rds2_c3">{{state.rds2_carrier3_level}}</span>
                                        <div class="text-[9px] text-gray-500 mt-1">Recommended: 4.5% (max 15%)</div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="bg-amber-900/20 border border-amber-500/30 rounded p-3 mt-4">
                            <div class="text-sm text-amber-300 font-semibold mb-1">⚠️ Important Notes</div>
                            <ul class="text-xs text-gray-300 space-y-1">
                                <li>• Keep total MPX modulation below 100% to avoid overdeviation</li>
                                <li>• Monitor output with a spectrum analyzer to verify carrier levels</li>
                                <li>• RDS2 carriers currently transmit test patterns for verification</li>
                                <li>• Quadrature phase shifting reduces peak-to-average ratio</li>
                            </ul>
                        </div>
                    </div>
                </div>

                <div class="section border-l-4 border-l-indigo-500 mt-4">
                    <div class="section-header text-indigo-400">📸 Station Logo Upload (RDS2 File Transfer)</div>
                    <div class="section-body">
                        <div class="bg-indigo-900/20 border border-indigo-500/30 rounded p-3 mb-4">
                            <div class="text-xs text-gray-300">
                                Upload a station logo image for RDS2 file transfer (future feature).
                                Supported formats: PNG, JPG, GIF, BMP • Max size: 3 KB recommended
                            </div>
                        </div>

                        <div class="grid grid-cols-1 gap-4">
                            {% if state.rds2_logo_path %}
                            <div class="bg-green-900/20 border border-green-500/30 rounded p-3">
                                <div class="flex items-center justify-between mb-2">
                                    <div class="text-sm text-green-300 font-semibold">✓ Logo Uploaded</div>
                                    <button onclick="clearRDS2Logo()" class="bg-red-600 hover:bg-red-500 text-white text-xs rounded px-3 py-1">Clear</button>
                                </div>
                                <div class="text-xs text-gray-400 break-all">{{ state.get('rds2_logo_filename', state.rds2_logo_path.split('/')[-1].split('\\')[-1]) }}</div>
                                <div class="mt-2">
                                    {% set logo_file = state.get('rds2_logo_filename', state.rds2_logo_path.split('/')[-1].split('\\')[-1]) %}
                                    <img src="/uploads/{{ logo_file }}" 
                                         alt="Station Logo" 
                                         class="max-w-xs max-h-32 border border-gray-600 rounded"
                                         onerror="this.style.display='none'">
                                </div>
                            </div>
                            {% else %}
                            <div class="bg-gray-900/50 border border-gray-700 rounded p-3">
                                <div class="text-sm text-gray-400 mb-2">No logo uploaded</div>
                            </div>
                            {% endif %}

                            <div>
                                <label class="block text-sm mb-2">Upload New Logo</label>
                                <div class="flex gap-2">
                                    <input type="file" 
                                           id="rds2_logo_input" 
                                           accept="image/png,image/jpeg,image/gif,image/bmp"
                                           class="flex-1 bg-black/60 border border-gray-700 rounded px-3 py-2 text-sm text-gray-300 file:mr-4 file:py-1 file:px-3 file:rounded file:border-0 file:text-sm file:bg-purple-600 file:text-white hover:file:bg-purple-500">
                                    <button onclick="uploadRDS2Logo()" 
                                            class="bg-purple-600 hover:bg-purple-500 text-white rounded px-4 py-2 text-sm font-semibold">
                                        Upload
                                    </button>
                                </div>
                                <div class="text-xs text-gray-500 mt-1">PNG, JPG, GIF, or BMP format</div>
                            </div>

                            <div id="rds2_upload_status" class="hidden">
                                <div class="bg-blue-900/20 border border-blue-500/30 rounded p-3">
                                    <div class="text-sm text-blue-300" id="rds2_upload_message"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div id="datasets" class="content">
                <div class="section">
                    <div class="section-header">RDS Datasets</div>
                    <div class="section-body">
                        <p class="text-sm text-gray-400 mb-4">Save and switch between different RDS configurations. Each dataset stores all RDS settings.</p>

                        <div class="grid grid-cols-3 gap-3 mb-4" id="dataset_buttons">
                        </div>

                        <div class="flex gap-2">
                            <button onclick="createDataset()" class="bg-green-600 hover:bg-green-500 text-white rounded px-3 py-2 text-sm">+ New Dataset</button>
                            <button onclick="renameCurrentDataset()" class="bg-blue-600 hover:bg-blue-500 text-white rounded px-3 py-2 text-sm">Rename</button>
                            <button onclick="deleteCurrentDataset()" class="bg-red-600 hover:bg-red-500 text-white rounded px-3 py-2 text-sm">Delete</button>
                        </div>

                        <div class="mt-4 p-3 bg-black/40 rounded border border-gray-700">
                            <div class="text-xs text-gray-400">
                                <strong>Current Dataset:</strong> <span id="current_dataset_name">Dataset 1</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div id="settings" class="content">
                <div class="section">
                    <div class="section-header">Startup & Access</div>
                    <div class="section-body">
                        <div class="flex items-center justify-between mb-3">
                            <div>
                                <label>Auto-start encoder on launch</label>
                                <div class="text-[9px] text-gray-500">Default is enabled; uncheck to keep the encoder idle on boot.</div>
                            </div>
                            <input type="checkbox" class="toggle-checkbox" id="auto_start" {% if auto_start %}checked{% endif %}>
                        </div>
                        <div class="mb-2">
                            <label>Username</label>
                            <input type="text" id="auth_user" value="{{ auth_config.get('user','') }}" class="w-full bg-black/60 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-pink-500">
                        </div>
                        <div class="mb-3">
                            <label>New Password</label>
                            <input type="password" id="auth_pass" placeholder="Leave blank to keep current" class="w-full bg-black/60 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-pink-500">
                        </div>
                        <div class="mb-3">
                            <label>Site Name</label>
                            <div class="text-[9px] text-gray-500 mb-1">Displayed in login screen and page title (e.g., "POWER FM Burgas").</div>
                            <input type="text" id="site_name" value="{{site_name}}" placeholder="Where am I" class="w-full bg-black/60 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-pink-500">
                        </div>
                        <div class="mb-3">
                            <label>HTTP Port</label>
                            <div class="text-[9px] text-gray-500 mb-1">Port number for web interface (requires restart).</div>
                            <input type="number" id="http_port" min="1024" max="65535" value="{{http_port}}" onchange="sync()" class="w-full bg-black/60 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-pink-500">
                        </div>
                        <div class="flex gap-2 items-center">
                            <button onclick="saveSettings()" class="bg-pink-600 hover:bg-pink-500 text-white font-semibold rounded px-4 py-2 text-sm transition">Save Settings</button>
                            <a href="/logout" class="text-[12px] text-gray-300 hover:text-white underline">Logout</a>
                        </div>
                        <div id="settings_status" class="text-[11px] text-gray-400 mt-2"></div>
                    </div>
                </div>
            </div>

            <div id="uecp" class="content">
                <div class="section">
                    <div class="section-header">&#9112; UECP Server (Universal Encoder Communications Protocol)</div>
                    <div class="section-body">
                        <div class="flex items-start gap-2 mb-3">
                            <div class="flex-1">
                                <label>Enable UECP TCP Server</label>
                                <div class="text-[9px] text-gray-500">Accept UECP 6.02 connections to remotely update RDS parameters (PI, PS, PTY, TA/TP, DI, M/S, PIN, RadioText).</div>
                            </div>
                            <input type="checkbox" class="toggle-checkbox" id="uecp_enabled" {% if state.uecp_enabled %}checked{% endif %}>
                        </div>
                        <div class="grid grid-cols-2 gap-3 mb-3">
                            <div>
                                <label>Bind Address</label>
                                <input type="text" id="uecp_host" value="{{ state.get('uecp_host', '0.0.0.0') }}" placeholder="0.0.0.0">
                                <div class="text-[9px] text-gray-500 mt-1">Use 0.0.0.0 for all interfaces or 127.0.0.1 for local-only.</div>
                            </div>
                            <div>
                                <label>TCP Port</label>
                                <input type="number" id="uecp_port" value="{{ state.get('uecp_port', 4001) }}" min="1" max="65535">
                                <div class="text-[9px] text-gray-500 mt-1">Default UECP port is 4001.</div>
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-3 mb-3">
                            <div>
                                <label>PSN Filter</label>
                                <input type="number" id="uecp_psn" value="{{ state.get('uecp_psn', 0) }}" min="0" max="255">
                                <div class="text-[9px] text-gray-500 mt-1">Programme Service Number to accept (0 = accept all). Messages with PSN 0 always pass.</div>
                            </div>
                            <div>
                                <label>DSN Filter</label>
                                <input type="number" id="uecp_dsn" value="{{ state.get('uecp_dsn', 0) }}" min="0" max="255">
                                <div class="text-[9px] text-gray-500 mt-1">Dataset Number to accept (0 = accept all). Messages with DSN 0 always pass.</div>
                            </div>
                        </div>
                        <div class="border-t border-gray-700 pt-3 mb-3">
                            <div class="flex items-center justify-between mb-2">
                                <div>
                                    <label>Enable UECP WebSocket Client</label>
                                    <div class="text-[9px] text-gray-500">Connect to a WebSocket that publishes base64-encoded UECP frames (alternative to TCP server).</div>
                                </div>
                                <input type="checkbox" class="toggle-checkbox" id="uecp_ws_enabled" {% if state.get('uecp_ws_enabled') %}checked{% endif %}>
                            </div>
                            <div>
                                <label>WebSocket URL</label>
                                <input type="text" id="uecp_ws_url" value="{{ state.get('uecp_ws_url', 'ws://127.0.0.1/pacific') }}" placeholder="ws://192.168.1.10/pacific">
                                <div class="text-[9px] text-gray-500 mt-1">UECP frames must be published as base64-encoded text messages. Reconnects automatically on drop.</div>
                            </div>
                        </div>
                        <div class="p-3 bg-black/30 rounded border border-gray-700 mb-3 text-[10px] text-gray-400">
                            <div class="font-bold text-gray-300 mb-1">Supported UECP Message Elements</div>
                            <div class="grid grid-cols-2 gap-x-4">
                                <div>0x01 PI &mdash; Programme Identification</div>
                                <div>0x02 PS &mdash; Programme Service name</div>
                                <div>0x03 TA/TP &mdash; Traffic flags</div>
                                <div>0x04 DI &mdash; Decoder Identification</div>
                                <div>0x05 M/S &mdash; Music/Speech switch</div>
                                <div>0x06 PIN &mdash; Programme Item Number</div>
                                <div>0x07 PTY &mdash; Programme Type</div>
                                <div>0x0A RT &mdash; RadioText (Group 2A)</div>
                                <div>0x13 AF &mdash; Alternative Frequencies</div>
                                <div>0x1A SLC &mdash; Slow Labelling Codes (ECC/LIC)</div>
                                <div>0x24 FFG &mdash; Free Format Group (any group, cyclic or one-shot)</div>
                                <div>0x40 ODA_SET &mdash; ODA Application Assignment (Group 3A)</div>
                                <div>0x46 ODA_DATA &mdash; ODA Data (group data for registered AID)</div>
                            </div>
                            <div class="mt-2 text-gray-500">Note: receiving an RT update (0x0A) automatically clears the RT Messages list so that UECP has full control of RadioText.</div>
                        </div>
                        <div class="flex gap-2 items-center">
                            <button onclick="saveUECPSettings()" class="bg-pink-600 hover:bg-pink-500 text-white font-semibold rounded px-4 py-2 text-sm transition">Apply</button>
                        </div>
                        <div id="uecp_status" class="text-[11px] text-gray-400 mt-2"></div>
                    </div>
                </div>
            </div>

        </div>
    </div>

    <!-- RT+ Builder Modal -->
    <div id="rtplus_modal" class="rtplus-modal-overlay" style="display: none;">
        <div class="rtplus-modal">
            <div class="rtplus-modal-header">
                <div>
                    <div class="text-lg font-bold text-white">RT+ Message Builder</div>
                    <div class="text-xs text-gray-400">Build RadioText with tagged content (supports all 64 RT+ content types)</div>
                </div>
                <button onclick="closeRTPlusModal()" class="text-gray-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <div class="rtplus-modal-body space-y-4">
                <!-- Buffer Tabs (only shown in manual buffer mode) -->
                <div id="builder_buffer_tabs" class="flex gap-2" style="display: none;">
                    <button id="builder_tab_a" onclick="setBuilderBuffer('a')" class="px-4 py-2 rounded font-bold bg-[#d946ef] text-white">Buffer A</button>
                    <button id="builder_tab_b" onclick="setBuilderBuffer('b')" class="px-4 py-2 rounded font-bold bg-[#333] text-gray-400 hover:bg-[#444]">Buffer B</button>
                </div>

                <!-- Split Mode -->
                <div class="bg-[#252525] border border-[#333] rounded p-3">
                    <div class="flex items-center justify-between mb-2">
                        <div>
                            <label class="text-xs text-gray-400 font-bold">SPLIT MODE</label>
                            <div class="text-[10px] text-gray-500">Parse existing RT text into tagged segments</div>
                        </div>
                        <button onclick="showSplitPanel()" id="btn_show_split" class="px-3 py-1 bg-[#333] hover:bg-[#444] rounded text-xs text-gray-300">Split Text...</button>
                    </div>
                    <div id="split_panel" class="hidden mt-3 space-y-2 border-t border-[#444] pt-3">
                        <div class="flex gap-2 items-center">
                            <label class="w-20 text-xs text-gray-500 shrink-0">Source:</label>
                            <input type="text" id="split_source" class="flex-1 bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" placeholder="Artist Name - Song Title">
                        </div>
                        <div class="flex gap-2 items-center">
                            <label class="w-20 text-xs text-gray-500 shrink-0">Split at:</label>
                            <input type="text" id="split_delimiter" class="w-24 bg-[#111] border border-[#444] rounded px-2 py-1 text-sm text-center" value=" - ">
                            <button onclick="performSplit()" class="px-3 py-1 bg-[#d946ef] hover:bg-[#c026d3] rounded text-xs text-white font-bold">Split</button>
                            <button onclick="hideSplitPanel()" class="px-3 py-1 bg-[#333] hover:bg-[#444] rounded text-xs text-gray-300">Cancel</button>
                        </div>
                        <div class="text-[10px] text-gray-500">Tip: Use \r"path" or \w"url" in source for dynamic content</div>
                    </div>
                </div>

                <!-- Message Construction -->
                <div class="bg-[#252525] border border-[#333] rounded p-4 space-y-3">
                    <div class="text-xs text-gray-400 mb-2">Build your message with up to 2 tagged segments:</div>

                    <!-- Prefix -->
                    <div class="flex gap-2 items-center">
                        <label class="w-20 text-xs text-gray-500 shrink-0">Prefix:</label>
                        <input type="text" id="builder_prefix" class="flex-1 bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" placeholder="Text before first tag" oninput="updateBuilderPreview()">
                    </div>

                    <!-- Tag 1 -->
                    <div class="bg-[#1a1a1a] border border-orange-900/50 rounded p-3 space-y-2">
                        <div class="flex gap-2 items-center">
                            <label class="w-20 text-xs text-orange-400 font-bold shrink-0">Tag 1:</label>
                            <select id="builder_tag1_type" class="flex-1 bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" onchange="updateBuilderPreview()"></select>
                        </div>
                        <div class="flex gap-2 items-center">
                            <label class="w-20 text-xs text-gray-500 shrink-0">Content:</label>
                            <input type="text" id="builder_tag1_text" class="flex-1 bg-[#111] border border-orange-900/50 rounded px-2 py-1 text-sm text-orange-300" placeholder="Text or \r&quot;path&quot; for file" oninput="updateBuilderPreview()">
                        </div>
                        <div class="text-[10px] text-gray-500">Supports: \r"path" (file), \R"path" (file uppercase), \w"url" (web)</div>
                    </div>

                    <!-- Middle -->
                    <div class="flex gap-2 items-center">
                        <label class="w-20 text-xs text-gray-500 shrink-0">Between:</label>
                        <input type="text" id="builder_middle" class="flex-1 bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" placeholder=" - " value=" - " oninput="updateBuilderPreview()">
                    </div>

                    <!-- Tag 2 -->
                    <div class="bg-[#1a1a1a] border border-cyan-900/50 rounded p-3 space-y-2">
                        <div class="flex gap-2 items-center">
                            <label class="w-20 text-xs text-cyan-400 font-bold shrink-0">Tag 2:</label>
                            <select id="builder_tag2_type" class="flex-1 bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" onchange="updateBuilderPreview()"></select>
                        </div>
                        <div class="flex gap-2 items-center">
                            <label class="w-20 text-xs text-gray-500 shrink-0">Content:</label>
                            <input type="text" id="builder_tag2_text" class="flex-1 bg-[#111] border border-cyan-900/50 rounded px-2 py-1 text-sm text-cyan-300" placeholder="Text or \r&quot;path&quot; for file" oninput="updateBuilderPreview()">
                        </div>
                        <div class="text-[10px] text-gray-500">Supports: \r"path" (file), \R"path" (file uppercase), \w"url" (web)</div>
                    </div>

                    <!-- Suffix -->
                    <div class="flex gap-2 items-center">
                        <label class="w-20 text-xs text-gray-500 shrink-0">Suffix:</label>
                        <input type="text" id="builder_suffix" class="flex-1 bg-[#111] border border-[#444] rounded px-2 py-1 text-sm" placeholder="Text after tags" oninput="updateBuilderPreview()">
                    </div>
                </div>

                <!-- RT+ Categories Reference -->
                <div class="bg-[#1a1a1a] border border-[#333] rounded p-3">
                    <div class="flex items-center justify-between mb-2">
                        <label class="text-xs text-purple-400 font-bold">RT+ CATEGORIES</label>
                        <button onclick="toggleCategoryReference()" id="toggle_categories" class="px-2 py-1 bg-[#333] hover:bg-[#444] rounded text-xs text-gray-300">Show Reference</button>
                    </div>
                    <div id="category_reference" class="hidden space-y-2">
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                            <div class="space-y-1">
                                <div class="text-purple-400 font-bold">🎵 Music & Audio</div>
                                <div class="text-gray-400 text-[10px] ml-3">Title, Artist, Album, Track, Composer, Genre...</div>
                                
                                <div class="text-green-400 font-bold">📻 Station Information</div>
                                <div class="text-gray-400 text-[10px] ml-3">Station names, programs, host, frequency...</div>
                                
                                <div class="text-blue-400 font-bold">📰 News & Information</div>
                                <div class="text-gray-400 text-[10px] ml-3">News, sports, weather, events, horoscope...</div>
                                
                                <div class="text-red-400 font-bold">📺 Media & Entertainment</div>
                                <div class="text-gray-400 text-[10px] ml-3">Cinema, TV, scene information...</div>
                            </div>
                            <div class="space-y-1">
                                <div class="text-cyan-400 font-bold">📞 Contact & Interaction</div>
                                <div class="text-gray-400 text-[10px] ml-3">Phone, SMS, email, chat, voting...</div>
                                
                                <div class="text-yellow-400 font-bold">🔧 Utility & Services</div>
                                <div class="text-gray-400 text-[10px] ml-3">DateTime, weather, traffic, URLs, ads...</div>
                                
                                <div class="text-pink-400 font-bold">📍 Location & Services</div>
                                <div class="text-gray-400 text-[10px] ml-3">Places, appointments, purchases...</div>
                                
                                <div class="text-gray-400 font-bold">⚙️ System & Reserved</div>
                                <div class="text-gray-400 text-[10px] ml-3">Dummy, comment, reserved types...</div>
                            </div>
                        </div>
                        <div class="text-[10px] text-gray-500 border-t border-gray-700 pt-2 mt-2">
                            Select tag types from the dropdowns above to see detailed descriptions. Hierarchical organization helps you find the right content type quickly.
                        </div>
                    </div>
                </div>

                <!-- Preview -->
                <div class="bg-[#000] border border-[#333] rounded p-4">
                    <div class="flex justify-between items-center mb-2">
                        <label class="text-xs text-gray-400 font-bold">PREVIEW</label>
                        <div class="text-xs">
                            <span id="builder_char_count" class="text-green-400">0</span>
                            <span class="text-gray-500">/</span>
                            <span id="builder_char_limit" class="text-gray-400">64</span>
                            <span class="text-gray-600 ml-1">chars</span>
                        </div>
                    </div>
                    <div id="builder_preview" class="rtplus-preview text-gray-300"></div>
                    <div class="mt-3 grid grid-cols-2 gap-2 text-xs">
                        <div class="text-orange-400">Tag 1: <span id="builder_tag1_info" class="text-orange-300">-</span></div>
                        <div class="text-orange-400">Type: <span id="builder_tag1_typename" class="text-orange-300">-</span></div>
                        <div class="text-cyan-400">Tag 2: <span id="builder_tag2_info" class="text-cyan-300">-</span></div>
                        <div class="text-cyan-400">Type: <span id="builder_tag2_typename" class="text-cyan-300">-</span></div>
                    </div>
                    <div class="mt-2 font-mono text-base text-gray-600 overflow-x-auto whitespace-nowrap">
                        0----5----10---15---20---25---30---35---40---45---50---55---60---
                    </div>
                </div>

                <!-- Mode Toggle -->
                <div class="flex items-center gap-4 bg-[#252525] border border-[#333] rounded p-3">
                    <span class="text-xs text-gray-400">RT+ Mode:</span>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="radio" name="rtplus_mode" value="format" class="accent-[#d946ef]" onchange="updateRTPlusMode()">
                        <span class="text-sm text-gray-300">Format String</span>
                        <span class="text-[10px] text-gray-500">(legacy)</span>
                    </label>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="radio" name="rtplus_mode" value="builder" class="accent-[#d946ef]" onchange="updateRTPlusMode()">
                        <span class="text-sm text-gray-300">Builder</span>
                        <span class="text-[10px] text-gray-500">(new)</span>
                    </label>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="radio" name="rtplus_mode" value="regex" class="accent-[#d946ef]" onchange="updateRTPlusMode()">
                        <span class="text-sm text-gray-300">Regex Rules</span>
                        <span class="text-[10px] text-gray-500">(pattern match)</span>
                    </label>
                </div>

                <!-- Regex Rules Panel (visible only in Regex mode) -->
                <div id="regex_rules_panel" style="display:none;" class="bg-[#252525] border border-[#333] rounded p-3 space-y-2">
                    <div class="flex items-center justify-between mb-1">
                        <div>
                            <label class="text-xs text-gray-400 font-bold">REGEX RULES</label>
                            <div class="text-[10px] text-gray-500 mt-0.5">Rules tried in order; first match wins. Capture groups: group&nbsp;1&nbsp;→&nbsp;Tag&nbsp;1, group&nbsp;2&nbsp;→&nbsp;Tag&nbsp;2. No groups → whole match → Tag&nbsp;1.</div>
                        </div>
                        <div class="flex gap-2 items-center">
                            <div id="regex_buf_tabs" class="flex gap-1" style="display:none;">
                                <button id="regex_tab_a" onclick="setRegexBuffer('a')" class="px-2 py-1 rounded text-xs font-bold bg-[#d946ef] text-white">A</button>
                                <button id="regex_tab_b" onclick="setRegexBuffer('b')" class="px-2 py-1 rounded text-xs font-bold bg-[#333] text-gray-400 hover:bg-[#444]">B</button>
                            </div>
                            <button onclick="addRegexRule()" class="px-3 py-1 bg-green-800 hover:bg-green-700 rounded text-xs text-white font-bold">+ Add Rule</button>
                        </div>
                    </div>
                    <div id="regex_rules_list" class="space-y-1 max-h-48 overflow-y-auto"></div>
                </div>
            </div>

            <div class="rtplus-modal-footer">
                <button onclick="resetBuilder()" class="px-3 py-2 bg-[#333] hover:bg-[#444] rounded text-sm text-gray-300">Reset</button>
                <div class="flex gap-2">
                    <button onclick="closeRTPlusModal()" class="px-4 py-2 bg-[#333] hover:bg-[#444] rounded text-sm text-gray-300">Cancel</button>
                    <button onclick="applyBuilderConfig()" class="px-4 py-2 bg-[#d946ef] hover:bg-[#c026d3] rounded text-sm text-white font-bold">Apply</button>
                </div>
            </div>
        </div>
    </div>

    <div id="eon_modal" class="rtplus-modal-overlay" style="display: none;">
        <div class="rtplus-modal-content" style="max-width: 600px;">
            <div class="flex justify-between items-center mb-4">
                <h3 id="eon_modal_title" class="text-lg font-bold">Manage EON Services</h3>
                <button onclick="closeEONModal()" class="text-2xl leading-none hover:text-pink-600">×</button>
            </div>

            <div class="mb-4">
                <div id="eon_service_list" class="space-y-2 mb-3">
                </div>
                <button onclick="addEONService()" class="bg-green-600 hover:bg-green-500 text-white rounded px-3 py-2 text-sm w-full">+ Add Service</button>
            </div>

            <div id="eon_edit_form" style="display: none;" class="border-t border-gray-700 pt-4 mt-4">
                <input type="hidden" id="eon_edit_idx" value="">

                <div class="space-y-3">
                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">PI Code (ON)</label>
                        <input type="text" id="eon_pi" maxlength="4" pattern="[0-9A-Fa-f]{4}" class="w-full bg-black border border-gray-600 rounded px-2 py-1 font-mono" placeholder="C201">
                        <div class="text-[10px] text-gray-500 mt-1">4-digit hex code</div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">PS Name (ON)</label>
                        <input type="text" id="eon_ps" maxlength="8" class="w-full bg-black border border-gray-600 rounded px-2 py-1 font-mono" placeholder="OTHER FM">
                        <div class="text-[10px] text-gray-500 mt-1">8 characters max</div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">PTY (ON)</label>
                        <select id="eon_pty" class="w-full bg-black border border-gray-600 rounded px-2 py-1">
                            <option value="0">None</option>
                            <option value="1">News</option>
                            <option value="2">Current Affairs</option>
                            <option value="3">Information</option>
                            <option value="4">Sport</option>
                            <option value="5">Education</option>
                            <option value="6">Drama</option>
                            <option value="7">Culture</option>
                            <option value="8">Science</option>
                            <option value="9">Varied</option>
                            <option value="10">Pop Music</option>
                            <option value="11">Rock Music</option>
                            <option value="12">Easy Listening</option>
                            <option value="13">Light Classical</option>
                            <option value="14">Serious Classical</option>
                            <option value="15">Other Music</option>
                            <option value="16">Weather</option>
                            <option value="17">Finance</option>
                            <option value="18">Children's</option>
                            <option value="19">Social Affairs</option>
                            <option value="20">Religion</option>
                            <option value="21">Phone-In</option>
                            <option value="22">Travel</option>
                            <option value="23">Leisure</option>
                            <option value="24">Jazz</option>
                            <option value="25">Country</option>
                            <option value="26">National Music</option>
                            <option value="27">Oldies</option>
                            <option value="28">Folk Music</option>
                            <option value="29">Documentary</option>
                            <option value="30">Alarm Test</option>
                            <option value="31">Alarm</option>
                        </select>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">AF List (ON)</label>
                        <input type="text" id="eon_af" class="w-full bg-black border border-gray-600 rounded px-2 py-1" placeholder="88.1, 101.5">
                        <div class="text-[10px] text-gray-500 mt-1">Comma-separated frequencies in MHz (Method A)</div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Mapped Frequencies (Variants 5-8, 10)</label>
                        <textarea id="eon_mapped" rows="3" class="w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs font-mono" placeholder="107.4, 90.4&#10;101.6, 95.3"></textarea>
                        <div class="text-[10px] text-gray-500 mt-1">One pair per line: tuned, other (max 4 pairs). 87.6-107.9 MHz</div>
                    </div>

                    <div>
                        <div class="flex items-center justify-between bg-black p-2 rounded">
                            <label class="text-xs text-gray-400">TP (ON)</label>
                            <input type="checkbox" class="toggle-checkbox" id="eon_tp">
                        </div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">UECP PSN</label>
                        <input type="number" id="eon_uecp_psn" min="0" max="255" value="0" class="w-full bg-black border border-gray-600 rounded px-2 py-1">
                        <div class="text-[10px] text-gray-500 mt-1">Programme Service Number used to route UECP messages to this EON service (0 = not used).</div>
                    </div>
                </div>

                <div class="flex justify-end gap-2 mt-4">
                    <button onclick="cancelEONEdit()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">Cancel</button>
                    <button onclick="saveEONService()" class="px-4 py-2 bg-purple-600 hover:bg-purple-500 rounded text-sm text-white font-bold">Save</button>
                </div>
            </div>

            <div class="flex justify-end mt-4">
                <button onclick="closeEONModal()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">Close</button>
            </div>
        </div>
    </div>

    <div id="dynamic_control_modal" class="rtplus-modal-overlay" style="display: none;">
        <div class="rtplus-modal-content" style="max-width: 700px;">
            <div class="flex justify-between items-center mb-4">
                <h3 id="dynamic_control_modal_title" class="text-lg font-bold">Basic Dynamic Control</h3>
                <button onclick="closeDynamicControlModal()" class="text-2xl leading-none hover:text-pink-600">×</button>
            </div>

            <div class="mb-4 bg-blue-900 border border-blue-700 rounded p-3">
                <div class="text-xs text-blue-200 mb-2">📡 Fetch JSON data and map fields to RDS parameters</div>
                <div class="text-[10px] text-blue-300">Examples: Toggle speech/music based on MS field, change PTY by program type, pass through PTYN text</div>
            </div>

            <div class="mb-4">
                <div id="dynamic_control_rule_list" class="space-y-2 mb-3">
                </div>
                <button onclick="addDynamicControlRule()" class="bg-green-600 hover:bg-green-500 text-white rounded px-3 py-2 text-sm w-full">+ Add Control Rule</button>
            </div>

            <div id="dynamic_control_edit_form" style="display: none;" class="border-t border-gray-700 pt-4 mt-4">
                <input type="hidden" id="dc_edit_idx" value="">

                <div class="space-y-3">
                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Rule Name</label>
                        <input type="text" id="dc_name" class="w-full bg-black border border-gray-600 rounded px-2 py-1" placeholder="e.g., Toggle Stereo">
                        <div class="text-[10px] text-gray-500 mt-1">Descriptive name for this rule</div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">JSON URL</label>
                        <div class="flex gap-2">
                            <input type="text" id="dc_url" class="flex-1 bg-black border border-gray-600 rounded px-2 py-1 font-mono text-xs" placeholder="http://localhost:8080/metadata.json">
                            <button onclick="testDynamicControlURL()" class="bg-blue-600 hover:bg-blue-500 text-white rounded px-3 py-1 text-xs whitespace-nowrap">🔍 Test & Browse</button>
                        </div>
                        <div class="text-[10px] text-gray-500 mt-1">HTTP/HTTPS endpoint that returns JSON</div>
                    </div>

                    <div id="dc_json_browser" style="display: none;" class="bg-gray-900 border border-gray-700 rounded p-3 max-h-64 overflow-y-auto">
                        <div class="flex justify-between items-center mb-2">
                            <div class="text-xs text-green-400">✓ JSON fetched successfully - Click a field to select it</div>
                            <button onclick="closeDynamicControlBrowser()" class="text-xs text-gray-400 hover:text-white">✕</button>
                        </div>
                        <div id="dc_json_tree" class="text-xs font-mono"></div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Selected JSON Field</label>
                        <input type="text" id="dc_field_path" readonly class="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 font-mono text-xs text-cyan-400" placeholder="Click 'Test & Browse' to select a field">
                        <div class="text-[10px] text-gray-500 mt-1">Selected field path (e.g., data.audio.stereo)</div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">RDS Parameter</label>
                        <select id="dc_rds_param" class="w-full bg-black border border-gray-600 rounded px-2 py-1" onchange="updateDynamicControlParamUI()">
                            <option value="">Select parameter...</option>
                            <option value="ms">MS (Music/Speech)</option>
                            <option value="pty">PTY (Programme Type)</option>
                            <option value="ptyn">PTYN (Programme Type Name)</option>
                            <option value="tp">TP (Traffic Programme)</option>
                            <option value="ta">TA (Traffic Announcement)</option>
                            <option value="ari_announcement">ARI Announcement (DK)</option>
                            <option value="pi">PI (Programme Identification)</option>
                            <option value="en_pin">PIN Enable</option>
                            <option value="pin_day">PIN Day (1-31)</option>
                            <option value="pin_hour">PIN Hour (0-23)</option>
                            <option value="pin_minute">PIN Minute (0-59)</option>
                            <option value="tdc_5a_text">TDC 5A Text</option>
                            <option value="tdc_5b_text">TDC 5B Text</option>
                            <option value="en_tdc_5a">TDC 5A Enable</option>
                            <option value="en_tdc_5b">TDC 5B Enable</option>
                        </select>
                    </div>

                    <div id="dc_mapping_section">
                        <div id="dc_quick_setup" style="display: none;" class="bg-gradient-to-r from-blue-900 to-blue-800 border border-blue-600 rounded p-3 mb-3">
                            <div class="text-sm font-bold text-blue-200 mb-2">⚡ Quick Setup</div>
                            <div id="dc_quick_suggestions" class="space-y-2"></div>
                        </div>

                        <div id="dc_mapping_auto" style="display: none;">
                            <label class="text-xs text-gray-400 mb-1 block">Mapping Type</label>
                            <select id="dc_mapping_type" class="w-full bg-black border border-gray-600 rounded px-2 py-1" onchange="updateDynamicControlParamUI()">
                                <option value="direct">Direct (0/1 → 0/1)</option>
                                <option value="boolean">Boolean (true/false → 1/0)</option>
                                <option value="text_match">Text Match (custom mapping)</option>
                                <option value="conditional">Conditional (If X then Y, else default)</option>
                                <option value="list_match">List Match (any of a list → one output)</option>
                                <option value="passthrough">Pass Through (JSON value → RDS value)</option>
                            </select>
                        </div>

                        <div id="dc_pty_mapping" style="display: none;">
                            <label class="text-xs text-gray-400 mb-1 block">PTY Mapping</label>
                            <div class="bg-gray-900 border border-gray-700 rounded p-2 max-h-48 overflow-y-auto space-y-1" id="dc_pty_list"></div>
                            <div class="text-[10px] text-gray-500 mt-1">Map JSON values to PTY codes - click to add mappings</div>
                        </div>

                        <div id="dc_custom_mapping_section" style="display: none;">
                            <label class="text-xs text-gray-400 mb-1 block">Value Mappings</label>
                            <div id="dc_current_mappings" class="mb-2" style="display: none;"></div>
                            <div id="dc_value_mappings" class="space-y-2 mb-2"></div>
                            <button onclick="addDynamicControlValueMapping()" class="bg-gray-700 hover:bg-gray-600 text-white rounded px-2 py-1 text-xs w-full">+ Add Mapping</button>
                            <div class="text-[10px] text-gray-500 mt-1">Map specific JSON values to RDS values</div>
                        </div>

                        <div id="dc_conditional_section" style="display: none;">
                            <div class="flex justify-between items-center mb-2">
                                <label class="text-xs text-gray-400">Conditional Mapping</label>
                                <button type="button" onclick="addDynamicConditionRow()" class="px-2 py-1 bg-green-800 hover:bg-green-700 rounded text-xs text-white font-bold">+ Add Condition</button>
                            </div>
                            <div id="dc_conditions_list" class="space-y-2 mb-2"></div>
                            <!-- Hidden inputs retained for backward compat with updateDynamicControlParamUI -->
                            <input type="hidden" id="dc_condition_value">
                            <input type="hidden" id="dc_output_value">
                            <select id="dc_output_pty" style="display:none"><option value="0">0</option><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option><option value="5">5</option><option value="6">6</option><option value="7">7</option><option value="8">8</option><option value="9">9</option><option value="10">10</option><option value="11">11</option><option value="12">12</option><option value="13">13</option><option value="14">14</option><option value="15">15</option><option value="16">16</option><option value="17">17</option><option value="18">18</option><option value="19">19</option><option value="20">20</option><option value="21">21</option><option value="22">22</option><option value="23">23</option><option value="24">24</option><option value="25">25</option><option value="26">26</option><option value="27">27</option><option value="28">28</option><option value="29">29</option><option value="30">30</option><option value="31">31</option></select>
                            <div class="text-[10px] text-gray-500 bg-blue-900/20 border border-blue-700/50 rounded p-2">
                                <strong>How it works:</strong> Conditions are checked in order; the first match applies its output value. If none match, no override is applied (falls back to your RDS settings). Add as many rows as you need — great for mapping many programme names.
                            </div>
                        </div>

                        <div id="dc_list_match_section" style="display: none;">
                            <label class="text-xs text-gray-400 mb-1 block">List Match Mapping</label>
                            <div class="space-y-2 mb-2">
                                <div>
                                    <label class="text-[10px] text-gray-500">Match values (one per line, case-insensitive):</label>
                                    <textarea id="dc_match_list" rows="4" placeholder="e.g.&#10;News&#10;News+&#10;Breaking News" class="w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs font-mono"></textarea>
                                </div>
                                <div>
                                    <label class="text-[10px] text-gray-500">When any value matches, set RDS param to:</label>
                                    <input type="text" id="dc_list_output" placeholder="e.g. 1" class="w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs">
                                </div>
                            </div>
                            <div class="text-[10px] text-gray-500 bg-purple-900/20 border border-purple-700/50 rounded p-2">
                                <strong>How it works:</strong> If the JSON field value matches any entry in the list, apply the output value. Otherwise, the override is cleared (falls back to your RDS settings). Useful when many programme names should trigger the same RDS value.
                            </div>
                        </div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Poll Interval (seconds)</label>
                        <input type="number" id="dc_poll_interval" min="1" max="300" value="5" class="w-full bg-black border border-gray-600 rounded px-2 py-1">
                        <div class="text-[10px] text-gray-500 mt-1">How often to fetch JSON (1-300 seconds)</div>
                    </div>

                    <div>
                        <div class="flex items-center justify-between bg-black p-2 rounded">
                            <label class="text-xs text-gray-400">Enabled</label>
                            <input type="checkbox" class="toggle-checkbox" id="dc_enabled" checked>
                        </div>
                    </div>
                </div>

                <div class="flex justify-end gap-2 mt-4">
                    <button onclick="cancelDynamicControlEdit()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">Cancel</button>
                    <button onclick="saveDynamicControlRule()" class="px-4 py-2 bg-purple-600 hover:bg-purple-500 rounded text-sm text-white font-bold">Save</button>
                </div>
            </div>

            <div class="flex justify-end mt-4">
                <button onclick="closeDynamicControlModal()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">Close</button>
            </div>
        </div>
    </div>

    <!-- Custom ODA Modal -->
    <div id="custom_oda_modal" class="rtplus-modal-overlay" style="display: none;">
        <div class="rtplus-modal-content" style="max-width: 600px;">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-lg font-bold">Manage Custom ODAs</h3>
                <button onclick="closeCustomODAModal()" class="text-2xl leading-none hover:text-pink-600">×</button>
            </div>

            <div class="mb-4 bg-amber-900 border border-amber-700 rounded p-3">
                <div class="text-xs text-amber-200 mb-2">⚠️ Use only registered AID codes from the RDS Forum</div>
                <div class="text-[10px] text-amber-300">Group 3A will cycle through active ODAs (e.g RT+) and your custom ODAs</div>
            </div>

            <div class="mb-4">
                <div id="custom_oda_list_modal" class="space-y-2 mb-3">
                </div>
                <button onclick="addCustomODA()" class="bg-green-600 hover:bg-green-500 text-white rounded px-3 py-2 text-sm w-full">+ Add Custom ODA</button>
            </div>

            <div id="custom_oda_edit_form" style="display: none;" class="border-t border-gray-700 pt-4 mt-4">
                <input type="hidden" id="oda_edit_idx" value="">

                <div class="space-y-3">
                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Application Name</label>
                        <input type="text" id="oda_name" class="w-full bg-black border border-gray-600 rounded px-2 py-1" placeholder="e.g., iTunes Tagging, TMC">
                        <div class="text-[10px] text-gray-500 mt-1">Descriptive name for this ODA</div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">AID (Application ID)</label>
                        <input type="text" id="oda_aid" maxlength="4" pattern="[0-9A-Fa-f]{4}" class="w-full bg-black border border-gray-600 rounded px-2 py-1 font-mono" placeholder="4BD7">
                        <div class="text-[10px] text-gray-500 mt-1">4-digit hex code (e.g., 4BD7 for RT+, 0093 for DAB)</div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Group Type</label>
                        <select id="oda_group_type" class="w-full bg-black border border-gray-600 rounded px-2 py-1">
                            <option value="0">0A</option>
                            <option value="1">0B</option>
                            <option value="2">1A</option>
                            <option value="3">1B</option>
                            <option value="4">2A</option>
                            <option value="5">2B</option>
                            <option value="6">3A</option>
                            <option value="7">3B</option>
                            <option value="8">4A</option>
                            <option value="9">4B</option>
                            <option value="10">5A</option>
                            <option value="11">5B</option>
                            <option value="12">6A</option>
                            <option value="13">6B</option>
                            <option value="14">7A</option>
                            <option value="15">7B</option>
                            <option value="16">8A</option>
                            <option value="17">8B</option>
                            <option value="18">9A</option>
                            <option value="19">9B</option>
                            <option value="20">10A</option>
                            <option value="21">10B</option>
                            <option value="22">11A</option>
                            <option value="23">11B</option>
                            <option value="24">12A</option>
                            <option value="25">12B</option>
                            <option value="26">13A</option>
                            <option value="27">13B</option>
                            <option value="28">14A</option>
                            <option value="29">14B</option>
                            <option value="30">15A</option>
                            <option value="31">15B</option>
                        </select>
                        <div class="text-[10px] text-gray-500 mt-1">Which group type carries this ODA (e.g., 11A for RT+)</div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Message Bits (Optional)</label>
                        <input type="text" id="oda_msg" maxlength="4" pattern="[0-9A-Fa-f]{4}" class="w-full bg-black border border-gray-600 rounded px-2 py-1 font-mono" placeholder="0000">
                        <div class="text-[10px] text-gray-500 mt-1">4-digit hex for Block 3 (usually 0000)</div>
                    </div>

                    <div>
                        <div class="flex items-center justify-between bg-black p-2 rounded">
                            <label class="text-xs text-gray-400">Enabled</label>
                            <input type="checkbox" class="toggle-checkbox" id="oda_enabled" checked>
                        </div>
                    </div>
                </div>

                <div class="flex justify-end gap-2 mt-4">
                    <button onclick="cancelCustomODAEdit()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">Cancel</button>
                    <button onclick="saveCustomODA()" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 rounded text-sm text-white font-bold">Save</button>
                </div>
            </div>

            <div class="flex justify-end mt-4">
                <button onclick="closeCustomODAModal()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">Close</button>
            </div>
        </div>
    </div>


    <div id="custom_groups_modal" class="rtplus-modal-overlay" style="display: none;">
        <div class="rtplus-modal-content" style="max-width: 600px;">
            <div class="flex justify-between items-center mb-4">
                <h3 id="custom_groups_modal_title" class="text-lg font-bold">Manage Custom Groups</h3>
                <button onclick="closeCustomGroupsModal()" class="text-2xl leading-none hover:text-pink-600">×</button>
            </div>

            <div class="mb-4">
                <div class="text-xs text-gray-400 mb-2">Your custom groups (click to expand):</div>
                <div id="custom_groups_list" class="space-y-2 mb-3 max-h-96 overflow-y-auto border border-gray-700 rounded p-2 bg-black">
                </div>
                <button onclick="addCustomGroup()" class="bg-green-600 hover:bg-green-500 text-white rounded px-3 py-2 text-sm w-full">+ Add Custom Group</button>
            </div>

            <div id="custom_group_edit_form" style="display: none;" class="border-t border-gray-700 pt-4 mt-4">
                <input type="hidden" id="cg_edit_idx" value="">

                <div class="space-y-3">
                    <div class="flex items-center justify-between bg-black p-2 rounded">
                        <label class="text-xs text-gray-400">Enabled</label>
                        <input type="checkbox" class="toggle-checkbox" id="cg_enabled" checked>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Group Type</label>
                        <select id="cg_type" class="w-full bg-black border border-gray-600 rounded px-2 py-1">
                            <option value="0">0 - Basic tuning & switching</option>
                            <option value="1">1 - Programme Item Number</option>
                            <option value="2">2 - RadioText</option>
                            <option value="3">3 - Application ID (ODA)</option>
                            <option value="4">4 - Clock-time & date</option>
                            <option value="5">5 - Transparent data channels</option>
                            <option value="6">6 - In-house applications</option>
                            <option value="7">7 - Radio Paging</option>
                            <option value="8">8 - Traffic Message Channel</option>
                            <option value="9">9 - Emergency warning</option>
                            <option value="10">10 - Programme Type Name</option>
                            <option value="11">11 - Open Data App (RT+)</option>
                            <option value="12">12 - ODA (DAB linkage)</option>
                            <option value="13">13 - Enhanced Paging</option>
                            <option value="14">14 - Enhanced Other Networks</option>
                            <option value="15">15 - Long Programme Service</option>
                        </select>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Version (A/B)</label>
                        <select id="cg_version" class="w-full bg-black border border-gray-600 rounded px-2 py-1">
                            <option value="0">A (Version 0)</option>
                            <option value="1">B (Version 1)</option>
                        </select>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Block 2 Tail (5 bits)</label>
                        <input type="text" id="cg_b2_tail" maxlength="2" pattern="[0-9A-Fa-f]{1,2}" class="w-full bg-black border border-gray-600 rounded px-2 py-1 font-mono" placeholder="00" value="00">
                        <div class="text-[10px] text-gray-500 mt-1">Hex value 0x00-0x1F (0-31)</div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Block 3 (16 bits)</label>
                        <input type="text" id="cg_b3" maxlength="4" pattern="[0-9A-Fa-f]{1,4}" class="w-full bg-black border border-gray-600 rounded px-2 py-1 font-mono" placeholder="0000" value="0000">
                        <div class="text-[10px] text-gray-500 mt-1">Hex value 0x0000-0xFFFF</div>
                    </div>

                    <div>
                        <label class="text-xs text-gray-400 mb-1 block">Block 4 (16 bits)</label>
                        <input type="text" id="cg_b4" maxlength="4" pattern="[0-9A-Fa-f]{1,4}" class="w-full bg-black border border-gray-600 rounded px-2 py-1 font-mono" placeholder="0000" value="0000">
                        <div class="text-[10px] text-gray-500 mt-1">Hex value 0x0000-0xFFFF</div>
                    </div>
                </div>

                <div class="flex justify-end gap-2 mt-4">
                    <button onclick="cancelCustomGroupEdit()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">Cancel</button>
                    <button onclick="saveCustomGroup()" class="px-4 py-2 bg-purple-600 hover:bg-purple-500 rounded text-sm text-white font-bold">Save</button>
                </div>
            </div>

            <div class="border-t border-gray-700 pt-4 mt-4">
                <div class="text-xs text-gray-400 mb-2">Import/Export</div>
                <div class="grid grid-cols-2 gap-2 mb-3">
                    <button onclick="exportCustomGroups()" class="px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded text-sm">Export JSON</button>
                    <button onclick="showImportDialog()" class="px-3 py-2 bg-green-600 hover:bg-green-500 rounded text-sm">Import</button>
                </div>
            </div>

            <div class="flex justify-end mt-4">
                <button onclick="closeCustomGroupsModal()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">Close</button>
            </div>
        </div>
    </div>

    <div id="import_modal" class="rtplus-modal-overlay" style="display: none;">
        <div class="rtplus-modal-content" style="max-width: 600px;">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-lg font-bold">Import Custom Groups</h3>
                <button onclick="closeImportDialog()" class="text-2xl leading-none hover:text-pink-600">×</button>
            </div>

            <div class="space-y-4">
                <div>
                    <label class="text-sm font-bold mb-2 block">Import Method</label>
                    <select id="import_method" class="w-full bg-black border border-gray-600 rounded px-2 py-1" onchange="updateImportMethod()">
                        <option value="text">Text/Paste (B2 B3 B4)</option>
                        <option value="rdsspy">RDS Spy Log File</option>
                        <option value="url">From URL</option>
                        <option value="json">JSON</option>
                    </select>
                </div>

                <div id="import_text_section">
                    <label class="text-sm font-bold mb-2 block">Paste Custom Groups Data</label>
                    <textarea id="import_text_data" rows="10" class="w-full bg-black border border-gray-600 rounded px-2 py-1 font-mono text-xs" placeholder="Format: B2 B3 B4&#10;Example:&#10;E0 594C 6201&#10;E1 4520 6201"></textarea>
                    <div class="text-xs text-gray-400 mt-2">
                        Format: <code>B2 B3 B4</code> (hex values)
                    </div>
                    <div class="grid grid-cols-2 gap-3 mt-3">
                        <div>
                            <label class="text-xs text-gray-400 mb-1 block">Default Group Type</label>
                            <input type="number" id="import_default_type" value="8" min="0" max="15" class="w-full bg-black border border-gray-600 rounded px-2 py-1">
                        </div>
                        <div>
                            <label class="text-xs text-gray-400 mb-1 block">Default Version</label>
                            <select id="import_default_version" class="w-full bg-black border border-gray-600 rounded px-2 py-1">
                                <option value="0">A (0)</option>
                                <option value="1">B (1)</option>
                            </select>
                        </div>
                    </div>
                </div>

                <div id="import_url_section" style="display: none;">
                    <label class="text-sm font-bold mb-2 block">URL to Fetch</label>
                    <input type="text" id="import_url" class="w-full bg-black border border-gray-600 rounded px-2 py-1" placeholder="https://example.com/groups.json">
                    <div class="text-xs text-gray-400 mt-2">
                        Supports JSON or text format
                    </div>
                    <div class="grid grid-cols-2 gap-3 mt-3">
                        <div>
                            <label class="text-xs text-gray-400 mb-1 block">Default Group Type</label>
                            <input type="number" id="import_url_default_type" value="8" min="0" max="15" class="w-full bg-black border border-gray-600 rounded px-2 py-1">
                        </div>
                        <div>
                            <label class="text-xs text-gray-400 mb-1 block">Default Version</label>
                            <select id="import_url_default_version" class="w-full bg-black border border-gray-600 rounded px-2 py-1">
                                <option value="0">A (0)</option>
                                <option value="1">B (1)</option>
                            </select>
                        </div>
                    </div>
                </div>

                <div id="import_json_section" style="display: none;">
                    <label class="text-sm font-bold mb-2 block">Paste JSON Data</label>
                    <textarea id="import_json_data" rows="10" class="w-full bg-black border border-gray-600 rounded px-2 py-1 font-mono text-xs" placeholder='[{"type": 8, "version": 0, "b2_tail": "1F", "b3": "CAFE", "b4": "BEEF", "enabled": true}]'></textarea>
                </div>

                <div id="import_rdsspy_section" style="display: none;">
                    <label class="text-sm font-bold mb-2 block">Paste RDS Spy Log Data</label>
                    <textarea id="import_rdsspy_data" rows="10" class="w-full bg-black border border-gray-600 rounded px-2 py-1 font-mono text-xs" placeholder="5158 052F 8749 4920 @2024/12/31 20:18:18.16&#10;5158 0528 E79E 2020 @2024/12/31 20:18:18.23&#10;5158 0527 9C56 2020 @2024/12/31 20:18:18.30" oninput="updateRdsSpyGroupGrid()"></textarea>
                    <div class="text-xs text-gray-400 mt-2">
                        Format: <code>PI B2 B3 B4 @timestamp</code> (RDS Spy log format)<br>
                        Group type and version automatically extracted from Block 2
                    </div>

                    <div class="mt-3 border-t border-gray-700 pt-3">
                        <div class="flex items-center justify-between mb-2">
                            <div class="text-xs font-bold text-gray-400">Detected Groups (click to toggle):</div>
                            <div class="flex gap-2">
                                <button onclick="selectAllRdsSpyGroups()" class="text-xs px-2 py-1 bg-purple-600 hover:bg-purple-700 rounded">Select All</button>
                                <button onclick="deselectAllRdsSpyGroups()" class="text-xs px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded">Deselect All</button>
                            </div>
                        </div>
                        <div class="text-[11px] text-gray-500 mb-2">
                            <span class="inline-block w-3 h-3 rounded" style="background: linear-gradient(135deg, #7c3aed 0%, #a855f7 100%);"></span> = Will import
                            <span class="mx-2">|</span>
                            <span class="inline-block w-3 h-3 rounded bg-gray-700"></span> = Will skip
                            <span class="mx-2">|</span>
                            <span id="rdsspy_enabled_count" class="text-cyan-400">0 groups selected</span>
                        </div>
                        <div id="rdsspy_group_grid" class="grid grid-cols-8 gap-2">
                            <!-- Grid will be populated by JavaScript -->
                        </div>
                    </div>
                </div>

                <div>
                    <label class="text-sm font-bold mb-2 block">Import Mode</label>
                    <div class="flex gap-2">
                        <label class="flex items-center">
                            <input type="radio" name="import_mode" value="replace" checked class="mr-2">
                            <span class="text-sm">Replace all</span>
                        </label>
                        <label class="flex items-center">
                            <input type="radio" name="import_mode" value="merge" class="mr-2">
                            <span class="text-sm">Merge with existing</span>
                        </label>
                    </div>
                </div>

                <div class="flex justify-end gap-2">
                    <button onclick="closeImportDialog()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">Cancel</button>
                    <button onclick="doImport()" class="px-4 py-2 bg-green-600 hover:bg-green-500 rounded text-sm text-white font-bold">Import</button>
                </div>
            </div>
        </div>
    </div>

    <!-- RDS Spy Preview Modal -->
    <div id="rdsspy_preview_modal" style="display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.8); z-index: 9999; justify-content: center; align-items: center;">
        <div style="background: #1a1a1a; border: 1px solid #444; border-radius: 8px; max-width: 900px; width: 90%; max-height: 80vh; overflow: auto; box-shadow: 0 4px 20px rgba(0,0,0,0.5);">
            <div style="padding: 20px;">
                <h3 class="text-xl font-bold mb-4 text-purple-400">RDS Spy Import Preview</h3>

                <div class="mb-4">
                    <div class="flex items-center justify-between mb-3">
                        <div>
                            <span class="text-sm text-gray-400">Select groups to import:</span>
                            <div class="text-xs text-gray-500 mt-1">💡 Click any group or item to toggle selection (bright = selected, dark = not selected)</div>
                        </div>
                        <div class="flex gap-2">
                            <button onclick="rdsspySelectAll()" class="px-3 py-1 bg-purple-600 hover:bg-purple-500 rounded text-xs">Select All</button>
                            <button onclick="rdsspyDeselectAll()" class="px-3 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs">Deselect All</button>
                        </div>
                    </div>
                    <div id="rdsspy_preview_list" class="space-y-2 max-h-96 overflow-y-auto border border-gray-700 rounded p-2 bg-black">
                        <!-- Preview items will be rendered here -->
                    </div>
                </div>

                <div class="mb-4">
                    <label class="text-sm font-bold mb-2 block">Import Mode</label>
                    <div class="flex gap-2">
                        <label class="flex items-center">
                            <input type="radio" name="rdsspy_import_mode" value="replace" checked class="mr-2">
                            <span class="text-sm">Replace all</span>
                        </label>
                        <label class="flex items-center">
                            <input type="radio" name="rdsspy_import_mode" value="merge" class="mr-2">
                            <span class="text-sm">Merge with existing</span>
                        </label>
                    </div>
                </div>

                <div class="flex justify-end gap-2">
                    <button onclick="closeRdsSpyPreview()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm">Cancel</button>
                    <button onclick="confirmRdsSpyImport()" class="px-4 py-2 bg-green-600 hover:bg-green-500 rounded text-sm text-white font-bold">Import Selected</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        var socket = io();
        var running = {{ 'true' if state.running else 'false' }};
        var pty_list_rds = {{ pty_list_rds|tojson }};
        var pty_list_rbds = {{ pty_list_rbds|tojson }};

        // RT+ Content Types Dictionary with category information
        var RTPLUS_TYPES = {
            0: ["Dummy", "No content type", "system"],
            1: ["Title", "Item title", "music"],
            2: ["Album", "Album/CD name", "music"],
            3: ["Track", "Track number", "music"],
            4: ["Artist", "Artist name", "music"],
            5: ["Composition", "Composition name", "music"],
            6: ["Movement", "Movement name", "music"],
            7: ["Conductor", "Conductor", "music"],
            8: ["Composer", "Composer", "music"],
            9: ["Band", "Band/Orchestra", "music"],
            10: ["Comment", "Free text comment", "system"],
            11: ["Genre", "Genre", "music"],
            12: ["News", "News headlines", "news"],
            13: ["News.Local", "Local news", "news"],
            14: ["Stock", "Stock market", "news"],
            15: ["Sport", "Sport news", "news"],
            16: ["Lottery", "Lottery numbers", "news"],
            17: ["Horoscope", "Horoscope", "news"],
            18: ["Daily", "Daily diversion", "news"],
            19: ["Health", "Health tips", "news"],
            20: ["Event", "Event info", "news"],
            21: ["Scene", "Scene/Film info", "media"],
            22: ["Cinema", "Cinema info", "media"],
            23: ["TV", "TV info", "media"],
            24: ["DateTime", "Date/Time", "utility"],
            25: ["Weather", "Weather info", "utility"],
            26: ["Traffic", "Traffic info", "utility"],
            27: ["Alarm", "Alarm/Emergency", "utility"],
            28: ["Advert", "Advertisement", "utility"],
            29: ["URL", "Website URL", "utility"],
            30: ["Other", "Other info", "utility"],
            31: ["Stn.Short", "Station name short", "station"],
            32: ["Stn.Long", "Station name long", "station"],
            33: ["Prog.Now", "Current program", "station"],
            34: ["Prog.Next", "Next program", "station"],
            35: ["Prog.Part", "Program part", "station"],
            36: ["Host", "Host name", "station"],
            37: ["Editorial", "Editorial staff", "station"],
            38: ["Frequency", "Frequency info", "station"],
            39: ["Homepage", "Homepage URL", "station"],
            40: ["Subchannel", "Sub-channel", "station"],
            41: ["Phone.Hotline", "Hotline phone", "contact"],
            42: ["Phone.Studio", "Studio phone", "contact"],
            43: ["Phone.Other", "Other phone", "contact"],
            44: ["SMS.Studio", "Studio SMS", "contact"],
            45: ["SMS.Other", "Other SMS", "contact"],
            46: ["Email.Hotline", "Hotline email", "contact"],
            47: ["Email.Studio", "Studio email", "contact"],
            48: ["Email.Other", "Other email", "contact"],
            49: ["MMS.Phone", "MMS number", "contact"],
            50: ["Chat", "Chat", "contact"],
            51: ["Chat.Centre", "Chat centre", "contact"],
            52: ["Vote.Question", "Vote question", "contact"],
            53: ["Vote.Centre", "Vote centre", "contact"],
            54: ["RFU", "Reserved", "system"],
            55: ["RFU", "Reserved", "system"],
            56: ["RFU", "Reserved", "system"],
            57: ["RFU", "Reserved", "system"],
            58: ["RFU", "Reserved", "system"],
            59: ["Place", "Place/Location", "location"],
            60: ["Appointment", "Appointment", "location"],
            61: ["Identifier", "Identifier", "location"],
            62: ["Purchase", "Purchase info", "location"],
            63: ["GetData", "Get Data", "location"]
        };

        // RT+ Categories for hierarchical organization
        var RTPLUS_CATEGORIES = {
            "music": {
                name: "🎵 Music & Audio",
                description: "Musical content identification",
                color: "text-purple-400"
            },
            "news": {
                name: "📰 News & Information", 
                description: "News, events, and informational content",
                color: "text-blue-400"
            },
            "media": {
                name: "📺 Media & Entertainment",
                description: "Film, TV, and entertainment content", 
                color: "text-red-400"
            },
            "station": {
                name: "📻 Station Information",
                description: "Radio station and program details",
                color: "text-green-400"
            },
            "contact": {
                name: "📞 Contact & Interaction",
                description: "Communication and voting services",
                color: "text-cyan-400"
            },
            "utility": {
                name: "🔧 Utility & Services",
                description: "General purpose and utility information",
                color: "text-yellow-400"
            },
            "location": {
                name: "📍 Location & Services",
                description: "Location-based and commercial services",
                color: "text-pink-400"
            },
            "system": {
                name: "⚙️ System & Reserved",
                description: "System types and reserved entries",
                color: "text-gray-400"
            }
        };

        // RT Messages State
        var rtMessages = [];
        try {
            var rtMessagesStr = {{ state.rt_messages|tojson }};
            rtMessages = JSON.parse(rtMessagesStr) || [];
        } catch(e) {
            console.error("Failed to parse rt_messages:", e);
            rtMessages = [];
        }
        var currentMsgBuffer = 'AB';

        // RT+ Builder State (legacy)
        var currentBuilderBuffer = 'a';
        var builderState = {
            a: { prefix: '', tag1_type: 4, tag1_text: '', middle: ' - ', tag2_type: 1, tag2_text: '', suffix: '' },
            b: { prefix: '', tag1_type: 4, tag1_text: '', middle: ' - ', tag2_type: 1, tag2_text: '', suffix: '' }
        };

        // === RT MESSAGE FUNCTIONS ===
        function renderRTMessages() {
            var container = document.getElementById('rt_messages_list');
            var emptyState = document.getElementById('rt_messages_empty');

            if (!rtMessages || rtMessages.length === 0) {
                container.innerHTML = '';
                emptyState.style.display = 'block';
                return;
            }

            emptyState.style.display = 'none';
            container.innerHTML = rtMessages.map(function(msg, idx) {
                return renderMessageCard(msg, idx);
            }).join('');

            // Attach drag-and-drop handlers after rendering
            attachDragHandlers();
        }

        var draggedIndex = -1;

        function attachDragHandlers() {
            var cards = document.querySelectorAll('.rt-msg-card');
            cards.forEach(function(card, idx) {
                card.draggable = true;

                card.addEventListener('dragstart', function(e) {
                    draggedIndex = idx;
                    card.classList.add('dragging');
                    e.dataTransfer.effectAllowed = 'move';
                });

                card.addEventListener('dragend', function(e) {
                    card.classList.remove('dragging');
                    // Remove all drag-over classes
                    document.querySelectorAll('.rt-msg-card').forEach(function(c) {
                        c.classList.remove('drag-over');
                    });
                });

                card.addEventListener('dragover', function(e) {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';

                    if (idx !== draggedIndex) {
                        card.classList.add('drag-over');
                    }
                });

                card.addEventListener('dragleave', function(e) {
                    card.classList.remove('drag-over');
                });

                card.addEventListener('drop', function(e) {
                    e.preventDefault();
                    card.classList.remove('drag-over');

                    if (idx !== draggedIndex && draggedIndex >= 0) {
                        // Reorder array
                        var draggedMsg = rtMessages[draggedIndex];
                        rtMessages.splice(draggedIndex, 1);
                        rtMessages.splice(idx, 0, draggedMsg);

                        // Re-render and sync
                        renderRTMessages();
                        syncRTMessages();
                    }
                });
            });
        }

        function renderMessageCard(msg, idx) {
            var bufferClass = 'buffer-' + msg.buffer.toLowerCase();
            var sourceIcon = {manual: '✏️', file: '📄', url: '🌐', json: '📊'}[msg.source_type] || '✏️';
            var preview = (msg.content || '').substring(0, 50) + ((msg.content || '').length > 50 ? '...' : '');
            var enabledClass = msg.enabled ? '' : 'disabled';

            return '<div class="rt-msg-card ' + enabledClass + '" data-id="' + msg.id + '">' +
                '<div class="rt-msg-header" onclick="editRTMessage(\'' + msg.id + '\')">' +
                '<span class="rt-msg-buffer ' + bufferClass + '">' + msg.buffer + '</span>' +
                '<span class="rt-msg-duration">' + (msg.cycles || 2) + ' cycles</span>' +
                '<span class="rt-msg-source">' + sourceIcon + '</span>' +
                '<div class="rt-msg-preview">' + escapeHtml(preview) + '</div>' +
                (msg.rt_plus_enabled ? '<span class="rt-msg-rtplus">RT+</span>' : '') +
                '<div class="rt-msg-actions">' +
                '<button onclick="event.stopPropagation(); toggleRTMessage(\'' + msg.id + '\')" title="' + (msg.enabled ? 'Disable' : 'Enable') + '">' + (msg.enabled ? '👁' : '👁‍🗨') + '</button>' +
                '<button onclick="event.stopPropagation(); deleteRTMessage(\'' + msg.id + '\')" title="Delete">×</button>' +
                '</div></div></div>';
        }

        function escapeHtml(text) {
            if (text == null || text == undefined) return '';
            var div = document.createElement('div');
            div.textContent = String(text);
            return div.innerHTML;
        }

        var pendingNewMessage = null; // Track message being created

        function addRTMessage() {
            pendingNewMessage = {
                id: 'msg_' + Date.now(),
                buffer: 'AB',
                cycles: 2,
                source_type: 'manual',
                content: '',
                split_delimiter: ' - ',
                rt_plus_enabled: false,
                rt_plus_tags: { tag1_type: 4, tag2_type: 1 },
                enabled: true,
                prefix: '',
                suffix: '',
                tagging_policies: '[]',
                case_sensitive: false,
                whole_words: false,
                sample_text: ''
            };
            openMessageModal(pendingNewMessage, true);
        }

        function openMessageModal(msg, isNew) {
            document.getElementById('rt_msg_edit_id').value = msg.id;
            document.getElementById('rt_msg_modal_title').textContent = isNew ? 'Add Message' : 'Edit Message';
            document.getElementById('rt_msg_cycles').value = msg.cycles || 2;

            // Load text trim flags
            var el = document.getElementById('rt_msg_trim_parens'); if (el) el.checked = msg.trim_parens || false;
            el = document.getElementById('rt_msg_trim_brackets'); if (el) el.checked = msg.trim_brackets || false;
            el = document.getElementById('rt_msg_trim_semicolon'); if (el) el.checked = msg.trim_at_semicolon || false;
            el = document.getElementById('rt_msg_trim_maxlen'); if (el) el.checked = msg.trim_max_len || false;

            // Set source type radio first
            var sourceRadios = document.querySelectorAll('input[name="rt_msg_source"]');
            sourceRadios.forEach(function(r) { r.checked = (r.value === (msg.source_type || 'manual')); });

            // Set buffer buttons
            currentMsgBuffer = msg.buffer || 'AB';
            updateBufferButtons();

            // Set RT+ enabled toggle
            document.getElementById('rt_msg_rtplus_enabled').checked = msg.rt_plus_enabled || false;

            // Load fields based on source type
            if (msg.source_type === 'manual') {
                // Always clear both text fields first to prevent showing stale content
                document.getElementById('rt_msg_simple_text').value = '';
                if (document.getElementById('rt_msg_sample_text'))
                    document.getElementById('rt_msg_sample_text').value = '';

                if (msg.content && !msg.rt_plus_enabled) {
                    // Simple text mode - set the simple text field
                    document.getElementById('rt_msg_simple_text').value = msg.content || '';
                } else if (msg.rt_plus_enabled) {
                    // RT+ mode - set the sample text field
                    if (document.getElementById('rt_msg_sample_text'))
                        document.getElementById('rt_msg_sample_text').value = msg.sample_text || msg.content || '';
                    globalSampleText = msg.sample_text || msg.content || '';
                } else {
                    // New message with no content - leave fields blank
                    if (document.getElementById('rt_msg_sample_text'))
                        document.getElementById('rt_msg_sample_text').value = msg.sample_text || '';
                    globalSampleText = msg.sample_text || '';
                }

                // Load intelligent tagging settings
                if (document.getElementById('rt_msg_prefix'))
                    document.getElementById('rt_msg_prefix').value = msg.prefix || '';
                if (document.getElementById('rt_msg_suffix'))
                    document.getElementById('rt_msg_suffix').value = msg.suffix || '';
                if (document.getElementById('rt_msg_split_pattern'))
                    document.getElementById('rt_msg_split_pattern').value = msg.split_delimiter || ' - ';
                if (document.getElementById('rt_msg_before_tag'))
                    document.getElementById('rt_msg_before_tag').value = (msg.rt_plus_tags && msg.rt_plus_tags.tag1_type) || 4;
                if (document.getElementById('rt_msg_after_tag'))
                    document.getElementById('rt_msg_after_tag').value = (msg.rt_plus_tags && msg.rt_plus_tags.tag2_type) || 1;
                if (document.getElementById('rt_msg_case_sensitive'))
                    document.getElementById('rt_msg_case_sensitive').checked = msg.case_sensitive || false;
                if (document.getElementById('rt_msg_whole_words'))
                    document.getElementById('rt_msg_whole_words').checked = msg.whole_words || false;

                // Load smart rules (backward compatibility with old 'smart_rules' field)
                taggingPolicies = [];
                try {
                    if (msg.tagging_policies) {
                        taggingPolicies = JSON.parse(msg.tagging_policies) || [];
                    } else if (msg.smart_rules) {
                        taggingPolicies = JSON.parse(msg.smart_rules) || [];
                    }
                } catch(e) {}
                renderTaggingPolicies();
            } else if (msg.source_type === 'json') {
                document.getElementById('rt_msg_json_url').value = msg.content || '';
                
                // Restore field and tag selections
                if (document.getElementById('rt_msg_json_field1'))
                    document.getElementById('rt_msg_json_field1').value = msg.json_field1 || '';
                if (document.getElementById('rt_msg_json_field2'))
                    document.getElementById('rt_msg_json_field2').value = msg.json_field2 || '';
                if (document.getElementById('rt_msg_json_hide_if_blank'))
                    document.getElementById('rt_msg_json_hide_if_blank').checked = msg.json_hide_if_blank || false;
                if (document.getElementById('rt_msg_json_delimiter'))
                    document.getElementById('rt_msg_json_delimiter').value = msg.split_delimiter || ' - ';

                // Load tagging policies (backward compatibility with old 'smart_rules' field)
                taggingPolicies = [];
                try {
                    if (msg.tagging_policies) {
                        taggingPolicies = JSON.parse(msg.tagging_policies) || [];
                    } else if (msg.smart_rules) {
                        taggingPolicies = JSON.parse(msg.smart_rules) || [];
                    }
                } catch(e) {}
                renderTaggingPolicies();

                // Auto-fetch JSON structure first to populate the field cache
                var url = msg.content;

                if (url) {
                    fetch('/fetch-json-structure', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: url })
                    })
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (data.ok) {
                            jsonFieldsCache = data.fields;
                            displayJSONFields(data.fields, data.sample);
                            
                            // Pass saved tag types to prevent reset to defaults
                            var savedTag1Type = (msg.rt_plus_tags && msg.rt_plus_tags.tag1_type) || 4;
                            var savedTag2Type = (msg.rt_plus_tags && msg.rt_plus_tags.tag2_type) || 1;
                            populateJSONFieldDropdowns(data.fields, msg.json_field1, msg.json_field2, savedTag1Type, savedTag2Type);
                            document.getElementById('rt_msg_json_structure').style.display = 'block';
                            document.getElementById('rt_msg_json_config').style.display = 'block';
                            updateMsgPreview();
                        } else {
                            document.getElementById('rt_msg_json_structure').style.display = 'none';
                            document.getElementById('rt_msg_json_config').style.display = 'none';
                            updateMsgPreview();
                        }
                    })
                    .catch(function(err) {
                        document.getElementById('rt_msg_json_structure').style.display = 'none';
                        document.getElementById('rt_msg_json_config').style.display = 'none';
                        updateMsgPreview();
                    });
                }
            } else {
                // File/URL source with intelligent tagging
                document.getElementById('rt_msg_content').value = msg.content || '';
                if (document.getElementById('rt_msg_prefix'))
                    document.getElementById('rt_msg_prefix').value = msg.prefix || '';
                if (document.getElementById('rt_msg_suffix'))
                    document.getElementById('rt_msg_suffix').value = msg.suffix || '';
                if (document.getElementById('rt_msg_split_pattern'))
                    document.getElementById('rt_msg_split_pattern').value = msg.split_delimiter || ' - ';
                if (document.getElementById('rt_msg_before_tag'))
                    document.getElementById('rt_msg_before_tag').value = (msg.rt_plus_tags && msg.rt_plus_tags.tag1_type) || 4;
                if (document.getElementById('rt_msg_after_tag'))
                    document.getElementById('rt_msg_after_tag').value = (msg.rt_plus_tags && msg.rt_plus_tags.tag2_type) || 1;
                if (document.getElementById('rt_msg_case_sensitive'))
                    document.getElementById('rt_msg_case_sensitive').checked = msg.case_sensitive || false;
                if (document.getElementById('rt_msg_whole_words'))
                    document.getElementById('rt_msg_whole_words').checked = msg.whole_words || false;

                // Load tagging policies (backward compatibility with old 'smart_rules' field)
                taggingPolicies = [];
                try {
                    if (msg.tagging_policies) {
                        taggingPolicies = JSON.parse(msg.tagging_policies) || [];
                    } else if (msg.smart_rules) {
                        taggingPolicies = JSON.parse(msg.smart_rules) || [];
                    }
                } catch(e) {}
                renderTaggingPolicies();
            }

            updateSourceUI();
            updateRTPlusUI();
            renderTaggingPolicies(); // Render policies list
            updateMsgPreview();
            document.getElementById('rt_msg_modal').style.display = 'flex';
        }

        function editRTMessage(id) {
            var msg = rtMessages.find(function(m) { return m.id === id; });
            if (!msg) return;
            pendingNewMessage = null; // Clear any pending new message
            openMessageModal(msg, false);
        }

        function closeRTMsgModal() {
            pendingNewMessage = null; // Clear pending message if modal closed without saving
            document.getElementById('rt_msg_modal').style.display = 'none';
        }

        function setMsgBuffer(buf) {
            currentMsgBuffer = buf;
            updateBufferButtons();
        }

        function updateBufferButtons() {
            var activeColors = { A: 'bg-[#dc2626]', B: 'bg-[#2563eb]', AB: 'bg-[#7c3aed]', AUTO: 'bg-[#059669]' };
            ['A', 'B', 'AB', 'AUTO'].forEach(function(b) {
                var btn = document.getElementById('rt_msg_buf_' + b.toLowerCase());
                if (!btn) return;
                if (b === currentMsgBuffer) {
                    btn.className = 'px-4 py-2 rounded font-bold text-sm ' + activeColors[b] + ' text-white';
                } else {
                    btn.className = 'px-4 py-2 rounded font-bold text-sm bg-[#333] text-gray-400 hover:bg-[#444]';
                }
            });
        }

        function updateSourceUI() {
            var sourceType = document.querySelector('input[name="rt_msg_source"]:checked').value;
            var contentWrap = document.getElementById('rt_msg_content_wrap');
            var jsonWrap = document.getElementById('rt_msg_json_wrap');
            var manualSimple = document.getElementById('rt_msg_manual_simple');
            var manualBuilder = document.getElementById('rt_msg_manual_builder');
            var rtplusOptions = document.getElementById('rt_msg_rtplus_options');
            var tagInfo = document.getElementById('rt_msg_tag_info');

            if (sourceType === 'manual') {
                // Show manual input (simple or builder depending on RT+ toggle)
                contentWrap.style.display = 'none';
                if (jsonWrap) jsonWrap.style.display = 'none';
                updateRTPlusUI(); // This will handle showing simple vs builder
                tagInfo.style.display = 'grid';
            } else if (sourceType === 'json') {
                // Show JSON configuration
                contentWrap.style.display = 'none';
                if (jsonWrap) jsonWrap.style.display = 'block';
                if (manualSimple) manualSimple.style.display = 'none';
                if (manualBuilder) manualBuilder.style.display = 'none';
                tagInfo.style.display = 'grid';
            } else {
                // Show content input, hide manual inputs and JSON
                contentWrap.style.display = 'block';
                if (jsonWrap) jsonWrap.style.display = 'none';
                if (manualSimple) manualSimple.style.display = 'none';
                if (manualBuilder) manualBuilder.style.display = 'none';
                tagInfo.style.display = 'grid';

                var label = document.getElementById('rt_msg_content_label');
                var hint = document.getElementById('rt_msg_content_hint');

                if (sourceType === 'file') {
                    label.textContent = 'Content Source';
                    hint.textContent = 'Full path to text file (e.g., C:\\nowplaying.txt)';
                } else if (sourceType === 'url') {
                    label.textContent = 'Content Source';
                    hint.textContent = 'URL to fetch text from';
                }
            }
            updateRTPlusUI();
            updateMsgPreview();
        }

        function updateRTPlusUI() {
            var enabled = document.getElementById('rt_msg_rtplus_enabled').checked;
            var sourceType = document.querySelector('input[name="rt_msg_source"]:checked').value;
            var opts = document.getElementById('rt_msg_rtplus_options');
            var manualSimple = document.getElementById('rt_msg_manual_simple');
            var manualBuilder = document.getElementById('rt_msg_manual_builder');
            var simpleTextEl = document.getElementById('rt_msg_simple_text');
            var sampleTextEl = document.getElementById('rt_msg_sample_text');

            // Show/hide RT+ options based on enabled state (for all modes)
            if (opts) opts.style.display = enabled ? 'block' : 'none';

            // For manual mode: show simple text when RT+ disabled, sample text when RT+ enabled
            if (sourceType === 'manual') {
                if (!enabled) {
                    // RT+ disabled: transfer content from sample to simple and show simple text input
                    if (sampleTextEl && simpleTextEl && sampleTextEl.value && !simpleTextEl.value) {
                        simpleTextEl.value = sampleTextEl.value;
                    }
                    if (manualSimple) manualSimple.style.display = 'block';
                    if (manualBuilder) manualBuilder.style.display = 'none';
                } else {
                    // RT+ enabled: transfer content from simple to sample and show sample text builder
                    if (simpleTextEl && sampleTextEl && simpleTextEl.value && !sampleTextEl.value) {
                        sampleTextEl.value = simpleTextEl.value;
                    }
                    if (manualSimple) manualSimple.style.display = 'none';
                    if (manualBuilder) manualBuilder.style.display = 'block';
                }
            } else {
                // Hide manual builders for non-manual modes
                if (manualSimple) manualSimple.style.display = 'none';
                if (manualBuilder) manualBuilder.style.display = 'none';
            }
            updateMsgPreview();
        }

        // Cache for resolved content
        var resolvedContentCache = '';
        var resolvedTagPositions = null;
        var resolveDebounceTimer = null;
        var lastResolvePath = '';
        var lastResolveType = '';

        function fetchResolvedContent() {
            var sourceType = document.querySelector('input[name="rt_msg_source"]:checked').value;
            var content = sourceType === 'json' ? document.getElementById('rt_msg_json_url').value : document.getElementById('rt_msg_content').value || '';

            if (sourceType === 'manual' || !content) {
                resolvedContentCache = '';
                renderPreviewWithContent('');
                return;
            }

            var cacheKey = content + sourceType;

            if (cacheKey === lastResolvePath && resolvedContentCache) {
                renderPreviewWithContent(resolvedContentCache, resolvedTagPositions);
                return;
            }

            lastResolvePath = cacheKey;
            lastResolveType = sourceType;

            var previewEl = document.getElementById('rt_msg_preview');
            previewEl.innerHTML = '<span class="text-gray-500">Loading...</span>';
            var requestData = { source_type: sourceType, content: content };

            if (sourceType === 'json') {
                requestData.json_field1 = document.getElementById('rt_msg_json_field1') ? document.getElementById('rt_msg_json_field1').value : '';
                requestData.json_field2 = document.getElementById('rt_msg_json_field2') ? document.getElementById('rt_msg_json_field2').value : '';
                requestData.json_hide_if_blank = document.getElementById('rt_msg_json_hide_if_blank') ? document.getElementById('rt_msg_json_hide_if_blank').checked : false;
                requestData.split_delimiter = document.getElementById('rt_msg_json_delimiter') ? document.getElementById('rt_msg_json_delimiter').value : ' - ';
                var rtPlusEnabledEl = document.getElementById('rt_msg_rtplus_enabled');
                requestData.rt_plus_enabled = rtPlusEnabledEl ? rtPlusEnabledEl.checked : false;
                requestData.tagging_policies = JSON.stringify(taggingPolicies || []);
            }

            fetch('/resolve-content', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestData)
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.ok) {
                    resolvedContentCache = data.resolved || '';
                    resolvedTagPositions = data.tag_positions || null;
                    renderPreviewWithContent(resolvedContentCache, resolvedTagPositions);
                } else {
                    resolvedContentCache = '';
                    resolvedTagPositions = null;
                    console.error('Resolve content error:', data.error);
                    previewEl.innerHTML = '<span class="text-red-400">Error: ' + escapeHtml(data.error || 'Failed') + '</span>';
                }
            })
            .catch(function(err) {
                resolvedContentCache = '';
                resolvedTagPositions = null;
                console.error('Fetch error:', err);
                previewEl.innerHTML = '<span class="text-red-400">Error loading content: ' + escapeHtml(err.message || 'Unknown error') + '</span>';
            });
        }

        function updateMsgPreview() {
            var sourceType = document.querySelector('input[name="rt_msg_source"]:checked').value;

            if (sourceType === 'manual') {
                renderPreviewWithContent(null);
            } else if (sourceType === 'json') {
                clearTimeout(resolveDebounceTimer);
                resolveDebounceTimer = setTimeout(fetchResolvedContent, 500);
            } else {
                clearTimeout(resolveDebounceTimer);
                resolveDebounceTimer = setTimeout(fetchResolvedContent, 500);
            }
        }

        // Global variable to store tagging policies and current editing
        var taggingPolicies = [];
        var currentEditingPolicyId = null;

        function renderPreviewWithContent(resolvedContent, tagPositions) {
            var sourceType = document.querySelector('input[name="rt_msg_source"]:checked').value;
            var limit = document.getElementById('rt_mode').value === '2B' ? 32 : 64;
            var preview = '';
            var tag1Start = -1, tag1Len = 0, tag1Type = 0;
            var tag2Start = -1, tag2Len = 0, tag2Type = 0;
            var rtPlusEnabled = document.getElementById('rt_msg_rtplus_enabled').checked;
            var appliedRuleName = '';

            if (sourceType === 'manual') {
                if (!rtPlusEnabled) {
                    // Simple text mode
                    preview = document.getElementById('rt_msg_simple_text').value || '';
                    // Update character count
                    var countElem = document.getElementById('rt_msg_simple_count');
                    if (countElem) countElem.textContent = preview.length;
                } else {
                    // Intelligent tagging mode - get sample text from input field
                    var sampleTextEl = document.getElementById('rt_msg_sample_text');
                    var sampleText = sampleTextEl ? (sampleTextEl.value || '') : '';

                    var result = applyTaggingPolicies(sampleText);
                    preview = result.text;
                    tag1Start = result.tag1Start;
                    tag1Len = result.tag1Len;
                    tag1Type = result.tag1Type;
                    tag2Start = result.tag2Start;
                    tag2Len = result.tag2Len;
                    tag2Type = result.tag2Type;
                    appliedRuleName = result.appliedRule;
                }
            } else if (sourceType === 'json') {
                // JSON source type with tagging policies
                var url = document.getElementById('rt_msg_json_url').value || '';

                if (!url) {
                    preview = '(enter JSON URL and click Fetch & Analyze)';
                } else {
                    if (resolvedContent) {
                        preview = resolvedContent;
                        
                        // Get tag types from field mapping
                        var tag1TypeField = parseInt(document.getElementById('rt_msg_json_tag1_type') ? document.getElementById('rt_msg_json_tag1_type').value : '4');
                        var tag2TypeField = parseInt(document.getElementById('rt_msg_json_tag2_type') ? document.getElementById('rt_msg_json_tag2_type').value : '1');
                        var delimiter = document.getElementById('rt_msg_json_delimiter') ? document.getElementById('rt_msg_json_delimiter').value : ' - ';
                        
                        // Calculate base tag positions from delimiter split
                        var delimiterPos = resolvedContent.indexOf(delimiter);
                        if (delimiterPos !== -1) {
                            // Two fields present
                            tag1Start = 0;
                            tag1Len = delimiterPos;
                            tag1Type = tag1TypeField;
                            tag2Start = delimiterPos + delimiter.length;
                            tag2Len = resolvedContent.length - tag2Start;
                            tag2Type = tag2TypeField;
                            appliedRuleName = 'Field Mapping';
                        } else {
                            // Single field
                            tag1Start = 0;
                            tag1Len = resolvedContent.length;
                            tag1Type = tag1TypeField;
                            appliedRuleName = 'Field Mapping';
                        }
                        
                        // Apply tagging policies (may override base tags)
                        if (taggingPolicies && taggingPolicies.length > 0) {
                            var result = applyTaggingPolicies(resolvedContent);
                            
                            // If a policy was applied (has appliedPolicy set), use its tags
                            if (result.appliedPolicy) {
                                preview = result.text;
                                tag1Start = result.tag1Start;
                                tag1Len = result.tag1Len;
                                tag1Type = result.tag1Type;
                                tag2Start = result.tag2Start;
                                tag2Len = result.tag2Len;
                                tag2Type = result.tag2Type;
                                appliedRuleName = result.appliedPolicy;
                            }
                            // else: keep base field mapping tags
                        }
                    } else {
                        preview = '(loading...)';
                    }
                }
            } else {
                // File/URL source with intelligent tagging
                var path = document.getElementById('rt_msg_content').value || '';

                if (!path) {
                    preview = '(enter file path or URL)';
                } else if (resolvedContent) {
                    var result = applyTaggingPolicies(resolvedContent);
                    preview = result.text;
                    tag1Start = result.tag1Start;
                    tag1Len = result.tag1Len;
                    tag1Type = result.tag1Type;
                    tag2Start = result.tag2Start;
                    tag2Len = result.tag2Len;
                    tag2Type = result.tag2Type;
                    appliedRuleName = result.appliedPolicy;
                } else {
                    preview = '(no content)';
                }
            }

            // Truncate preview
            if (preview.length > limit) {
                preview = preview.substring(0, limit);
                // Adjust tag positions if they exceed limit
                if (tag1Start + tag1Len > limit) {
                    tag1Len = Math.max(0, limit - tag1Start);
                }
                if (tag2Start + tag2Len > limit) {
                    tag2Len = Math.max(0, limit - tag2Start);
                }
            }

            // Update char count
            var countEl = document.getElementById('rt_msg_char_count');
            var limitEl = document.getElementById('rt_msg_char_limit');
            countEl.textContent = preview.length;
            limitEl.textContent = limit;
            countEl.className = preview.length > limit ? 'text-red-400' : (preview.length > limit - 5 ? 'text-yellow-400' : 'text-green-400');

            // Build preview HTML with highlighted tags
            var previewEl = document.getElementById('rt_msg_preview');
            var html = '';
            if (rtPlusEnabled && (tag1Len > 0 || tag2Len > 0)) {
                for (var i = 0; i < preview.length; i++) {
                    var char = escapeHtml(preview[i]);
                    if (i >= tag1Start && i < tag1Start + tag1Len) {
                        html += '<span class="rtplus-tag-1">' + char + '</span>';
                    } else if (i >= tag2Start && i < tag2Start + tag2Len) {
                        html += '<span class="rtplus-tag-2">' + char + '</span>';
                    } else {
                        html += char;
                    }
                }
            } else {
                html = escapeHtml(preview);
            }
            previewEl.innerHTML = html || '<span class="text-gray-600">(empty)</span>';

            // Update tag info
            var tag1Info = document.getElementById('rt_msg_tag1_info');
            var tag2Info = document.getElementById('rt_msg_tag2_info');
            var tagInfoContainer = document.getElementById('rt_msg_tag_info');

            if (sourceType === 'manual' && !rtPlusEnabled) {
                if (tagInfoContainer) tagInfoContainer.style.display = 'none';
            } else {
                if (tagInfoContainer) tagInfoContainer.style.display = 'grid';
                
                // Show applied policy info
                var ruleInfoEl = document.getElementById('rt_msg_rule_applied');
                if (ruleInfoEl) {
                    if (appliedRuleName) {
                        ruleInfoEl.textContent = 'Current policy: ' + appliedRuleName;
                        ruleInfoEl.className = 'text-blue-400 text-xs';
                    } else {
                        ruleInfoEl.textContent = 'Current policy: Field Mapping';
                        ruleInfoEl.className = 'text-gray-400 text-xs';
                    }
                }
                
                // Update tag info
                if (tag1Len > 0) {
                    var t1Name = RTPLUS_TYPES[tag1Type] ? RTPLUS_TYPES[tag1Type][0] : 'Unknown';
                    tag1Info.textContent = t1Name + ' [' + tag1Start + '-' + (tag1Start + tag1Len - 1) + ']';
                } else {
                    tag1Info.textContent = '-';
                }
                if (tag2Len > 0) {
                    var t2Name = RTPLUS_TYPES[tag2Type] ? RTPLUS_TYPES[tag2Type][0] : 'Unknown';
                    tag2Info.textContent = t2Name + ' [' + tag2Start + '-' + (tag2Start + tag2Len - 1) + ']';
                } else {
                    tag2Info.textContent = '-';
                }
            }
        }

        function applyTaggingPolicies(content) {
            var result = {
                text: content,
                tag1Start: -1,
                tag1Len: 0,
                tag1Type: 0,
                tag2Start: -1,
                tag2Len: 0,
                tag2Type: 0,
                appliedPolicy: ''
            };

            var subTaggingMatches = 0; // Track how many sub-tagging policies have matched

            // Apply policies in order (allow multiple sub-tagging policies to apply)
            for (var i = 0; i < taggingPolicies.length; i++) {
                var policy = taggingPolicies[i];
                if (!policy.enabled) continue;

                if (policy.type === 'default') {
                    // Apply default policy settings
                    var prefix = policy.settings.prefix || '';
                    var suffix = policy.settings.suffix || '';
                    var splitPattern = policy.settings.split_pattern || ' - ';
                    
                    result.text = prefix + content + suffix;
                    result.appliedPolicy = policy.name;
                    result.tag1Type = parseInt(policy.settings.tag1_type) || 0;
                    result.tag2Type = parseInt(policy.settings.tag2_type) || 0;
                    
                    // Apply split tagging if pattern exists
                    if (splitPattern && content.indexOf(splitPattern) !== -1) {
                        var parts = content.split(splitPattern, 2);
                        if (parts.length >= 2) {
                            result.tag1Start = prefix.length;
                            result.tag1Len = parts[0].length;
                            result.tag2Start = prefix.length + parts[0].length + splitPattern.length;
                            result.tag2Len = parts[1].length;
                        }
                    } else {
                        // Tag entire content if no split pattern match
                        result.tag1Start = prefix.length;
                        result.tag1Len = content.length;
                    }
                    
                    continue; // Default policies don't stop processing
                } else if (policy.type === 'sub') {
                    // Check trigger condition first (if specified)
                    if (policy.settings.trigger_type && policy.settings.trigger_type !== 'none') {
                        var triggerMatches = false;
                        var triggerPattern = policy.settings.trigger_pattern || '';
                        
                        if (!triggerPattern) continue; // Skip if trigger type set but no pattern
                        
                        switch (policy.settings.trigger_type) {
                            case 'contains':
                                triggerMatches = content.toLowerCase().includes(triggerPattern.toLowerCase());
                                break;
                            case 'starts_with':
                                triggerMatches = content.toLowerCase().startsWith(triggerPattern.toLowerCase());
                                break;
                            case 'ends_with':
                                triggerMatches = content.toLowerCase().endsWith(triggerPattern.toLowerCase());
                                break;
                            case 'equals':
                                triggerMatches = content.toLowerCase() === triggerPattern.toLowerCase();
                                break;
                            case 'regex':
                                try {
                                    var triggerRegex = new RegExp(triggerPattern, 'i');
                                    triggerMatches = triggerRegex.test(content);
                                } catch (e) {
                                    console.warn('Invalid trigger regex in policy:', policy.name);
                                }
                                break;
                        }
                        
                        if (!triggerMatches) continue; // Skip this policy if trigger doesn't match
                    }
                    
                    // Check sub-tagging condition
                    var matches = false;
                    var pattern = policy.settings.pattern || '';
                    
                    switch (policy.settings.condition) {
                        case 'starts_with':
                            matches = content.toLowerCase().startsWith(pattern.toLowerCase());
                            break;
                        case 'ends_with':
                            matches = content.toLowerCase().endsWith(pattern.toLowerCase());
                            break;
                        case 'contains':
                            matches = content.toLowerCase().includes(pattern.toLowerCase());
                            break;
                        case 'equals':
                            matches = content.toLowerCase() === pattern.toLowerCase();
                            break;
                        case 'regex':
                            try {
                                var regex = new RegExp(pattern, 'i');
                                matches = regex.test(content);
                            } catch (e) {
                                console.warn('Invalid regex in policy:', policy.name);
                            }
                            break;
                    }
                    
                    if (matches) {
                        // Apply sub-tagging - support multiple sub-tagging policies
                        result.appliedPolicy = policy.name;

                        var tagContent = content;
                        var tagStart = 0;

                        // Apply tag action
                        switch (policy.settings.action) {
                            case 'tag_all':
                                tagStart = 0;
                                break;
                            case 'tag_after':
                                var afterIndex = content.indexOf(pattern);
                                if (afterIndex !== -1) {
                                    tagStart = afterIndex + pattern.length;
                                    tagContent = content.substring(tagStart);
                                }
                                break;
                            case 'tag_before':
                                var beforeIndex = content.indexOf(pattern);
                                if (beforeIndex !== -1) {
                                    tagStart = 0;
                                    tagContent = content.substring(0, beforeIndex);
                                }
                                break;
                            case 'tag_match':
                                var matchIndex = content.indexOf(pattern);
                                if (matchIndex !== -1) {
                                    tagStart = matchIndex;
                                    tagContent = pattern;
                                }
                                break;
                        }

                        // Strip pattern if requested
                        if (policy.settings.strip_pattern && policy.settings.action !== 'tag_match') {
                            tagContent = tagContent.replace(new RegExp(escapeRegex(pattern), 'gi'), '');
                        }

                        // Apply to tag1 or tag2 depending on match count
                        if (subTaggingMatches === 0) {
                            // First sub-tagging match → tag1
                            result.tag1Type = parseInt(policy.settings.tag_type) || 0;
                            result.tag1Start = tagStart;
                            result.tag1Len = tagContent.length;
                            subTaggingMatches++;
                        } else if (subTaggingMatches === 1) {
                            // Second sub-tagging match → tag2
                            result.tag2Type = parseInt(policy.settings.tag_type) || 0;
                            result.tag2Start = tagStart;
                            result.tag2Len = tagContent.length;
                            subTaggingMatches++;
                            // RT+ supports up to 2 tags, so stop after 2
                            return result;
                        }
                    }
                }
            }

            return result;
        }

        function escapeRegex(string) {
            return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        }

        // Global variable for sample text
        var globalSampleText = 'Artist - Song Title';

        function updateSampleText() {
            var sampleInput = document.getElementById('rt_msg_sample_text');
            if (sampleInput) {
                globalSampleText = sampleInput.value || 'Artist - Song Title';
            }
        }

        // Smart Rules Management Functions
        function addSmartRule() {
            var rule = {
                id: Date.now(),
                name: 'New Rule',
                condition: 'contains',
                pattern: '',
                tag1Type: 1,
                tag2Type: -1,
                enabled: true
            };
            smartRules.push(rule);
            renderSmartRules();
            updateMsgPreview();
        }

        function removeSmartRule(ruleId) {
            smartRules = smartRules.filter(function(r) { return r.id !== ruleId; });
            renderSmartRules();
            updateMsgPreview();
        }

        function updateSmartRule(ruleId, field, value) {
            var rule = smartRules.find(function(r) { return r.id === ruleId; });
            if (rule) {
                if (field === 'tag1Type' || field === 'tag2Type') {
                    rule[field] = parseInt(value);
                } else if (field === 'enabled') {
                    rule[field] = value;
                } else {
                    rule[field] = value;
                }
                updateMsgPreview();
            }
        }

        function moveSmartRule(ruleId, direction) {
            var index = smartRules.findIndex(function(r) { return r.id === ruleId; });
            if (index === -1) return;

            if (direction === 'up' && index > 0) {
                var temp = smartRules[index];
                smartRules[index] = smartRules[index - 1];
                smartRules[index - 1] = temp;
            } else if (direction === 'down' && index < smartRules.length - 1) {
                var temp = smartRules[index];
                smartRules[index] = smartRules[index + 1];
                smartRules[index + 1] = temp;
            }
            
            renderSmartRules();
            updateMsgPreview();
        }

        function renderSmartRules() {
            var container = document.getElementById('smart_rules_container');
            if (!container) return;

            container.innerHTML = '';

            if (smartRules.length === 0) {
                container.innerHTML = '<div class="text-center text-gray-500 text-xs py-4 border border-gray-700 rounded">No smart rules defined. Click "+ Add" to create your first rule.</div>';
                return;
            }

            smartRules.forEach(function(rule, index) {
                var ruleHtml = `
                    <div class="bg-[#1a1a1a] border border-purple-800/50 rounded p-2 space-y-2">
                        <div class="flex items-center gap-2">
                            <label class="flex items-center gap-1 cursor-pointer">
                                <input type="checkbox" ${rule.enabled ? 'checked' : ''} 
                                       onchange="updateSmartRule(${rule.id}, 'enabled', this.checked)"
                                       class="accent-purple-600">
                                <input type="text" value="${escapeHtml(rule.name)}" 
                                       onchange="updateSmartRule(${rule.id}, 'name', this.value)"
                                       class="bg-transparent border-none text-xs text-white font-bold outline-none"
                                       style="min-width: 80px;">
                            </label>
                            <div class="flex gap-1 ml-auto">
                                <button onclick="moveSmartRule(${rule.id}, 'up')" ${index === 0 ? 'disabled' : ''}
                                        class="w-5 h-5 text-xs bg-gray-700 hover:bg-gray-600 disabled:bg-gray-800 disabled:text-gray-600 rounded">↑</button>
                                <button onclick="moveSmartRule(${rule.id}, 'down')" ${index === smartRules.length - 1 ? 'disabled' : ''}
                                        class="w-5 h-5 text-xs bg-gray-700 hover:bg-gray-600 disabled:bg-gray-800 disabled:text-gray-600 rounded">↓</button>
                                <button onclick="removeSmartRule(${rule.id})" 
                                        class="w-5 h-5 text-xs bg-red-800 hover:bg-red-700 rounded text-white">×</button>
                            </div>
                        </div>
                        
                        <div class="grid grid-cols-1 lg:grid-cols-4 gap-2 text-xs">
                            <!-- Condition -->
                            <div class="space-y-1">
                                <label class="text-gray-400">When text</label>
                                <select onchange="updateSmartRule(${rule.id}, 'condition', this.value)" 
                                        class="w-full bg-[#111] border border-[#444] rounded px-1 py-1 text-xs">
                                    <option value="contains" ${rule.condition === 'contains' ? 'selected' : ''}>Contains</option>
                                    <option value="startswith" ${rule.condition === 'startswith' ? 'selected' : ''}>Starts with</option>
                                    <option value="endswith" ${rule.condition === 'endswith' ? 'selected' : ''}>Ends with</option>
                                    <option value="regex" ${rule.condition === 'regex' ? 'selected' : ''}>Regex</option>
                                </select>
                            </div>
                            
                            <!-- Pattern -->
                            <div class="space-y-1">
                                <label class="text-gray-400">Pattern</label>
                                <input type="text" value="${escapeHtml(rule.pattern)}" 
                                       onchange="updateSmartRule(${rule.id}, 'pattern', this.value)"
                                       placeholder="e.g. 'Live:', 'News'"
                                       class="w-full bg-[#111] border border-[#444] rounded px-1 py-1 text-xs">
                            </div>
                            
                            <!-- Tag 1 -->
                            <div class="space-y-1">
                                <label class="text-orange-400">Tag Type</label>
                                <select onchange="updateSmartRule(${rule.id}, 'tag1Type', this.value)" 
                                        class="w-full bg-[#111] border border-orange-900/50 rounded px-1 py-1 text-xs">
                                    <option value="-1" ${rule.tag1Type === -1 ? 'selected' : ''}>No Tag</option>
                                    <option value="1" ${rule.tag1Type === 1 ? 'selected' : ''}>1: Title</option>
                                    <option value="4" ${rule.tag1Type === 4 ? 'selected' : ''}>4: Artist</option>
                                    <option value="2" ${rule.tag1Type === 2 ? 'selected' : ''}>2: Album</option>
                                    <option value="33" ${rule.tag1Type === 33 ? 'selected' : ''}>33: Prog.Now</option>
                                    <option value="31" ${rule.tag1Type === 31 ? 'selected' : ''}>31: Stn.Short</option>
                                    <option value="32" ${rule.tag1Type === 32 ? 'selected' : ''}>32: Stn.Long</option>
                                    <option value="36" ${rule.tag1Type === 36 ? 'selected' : ''}>36: Host</option>
                                </select>
                            </div>
                            
                            <!-- Tag 2 -->
                            <div class="space-y-1">
                                <label class="text-cyan-400">Extra Tag (opt)</label>
                                <select onchange="updateSmartRule(${rule.id}, 'tag2Type', this.value)" 
                                        class="w-full bg-[#111] border border-cyan-900/50 rounded px-1 py-1 text-xs">
                                    <option value="-1" ${rule.tag2Type === -1 ? 'selected' : ''}>No Tag</option>
                                    <option value="1" ${rule.tag2Type === 1 ? 'selected' : ''}>1: Title</option>
                                    <option value="4" ${rule.tag2Type === 4 ? 'selected' : ''}>4: Artist</option>
                                    <option value="2" ${rule.tag2Type === 2 ? 'selected' : ''}>2: Album</option>
                                    <option value="33" ${rule.tag2Type === 33 ? 'selected' : ''}>33: Prog.Now</option>
                                    <option value="32" ${rule.tag2Type === 32 ? 'selected' : ''}>32: Stn.Long</option>
                                    <option value="36" ${rule.tag2Type === 36 ? 'selected' : ''}>36: Host</option>
                                </select>
                            </div>
                        </div>
                    </div>
                `;
                container.innerHTML += ruleHtml;
            });
        }

        // Tagging Policy Management Functions
        function addTaggingPolicy() {
            var policy = {
                id: Date.now(),
                name: 'New Policy',
                type: 'default',
                enabled: true,
                settings: {
                    // Default policy settings
                    tag1_type: 4,
                    tag2_type: 1,
                    split_pattern: ' - ',
                    prefix: '',
                    suffix: '',
                    // Sub-tagging settings
                    trigger_type: 'none',
                    trigger_pattern: '',
                    condition: 'starts_with',
                    pattern: '',
                    action: 'tag_all',
                    tag_type: 1,
                    strip_pattern: false
                }
            };
            taggingPolicies.push(policy);
            renderTaggingPolicies();
            editTaggingPolicy(policy.id);
        }

        function editTaggingPolicy(policyId) {
            var policy = taggingPolicies.find(function(p) { return p.id === policyId; });
            if (!policy) return;

            currentEditingPolicyId = policyId;
            
            // Populate editor
            document.getElementById('policy_name').value = policy.name;
            document.getElementById('policy_type').value = policy.type;
            
            // Populate tag type selectors with hierarchical categories
            populateRTPlusSelect(document.getElementById('policy_default_tag1'), policy.settings.tag1_type || 4);
            populateRTPlusSelect(document.getElementById('policy_default_tag2'), policy.settings.tag2_type || 1);
            populateRTPlusSelect(document.getElementById('policy_sub_tag_type'), policy.settings.tag_type || 1);
            
            // Default settings
            document.getElementById('policy_default_split').value = policy.settings.split_pattern || ' - ';
            document.getElementById('policy_default_prefix').value = policy.settings.prefix || '';
            document.getElementById('policy_default_suffix').value = policy.settings.suffix || '';
            
            // Sub-tagging settings
            document.getElementById('policy_sub_trigger_type').value = policy.settings.trigger_type || 'none';
            document.getElementById('policy_sub_trigger_pattern').value = policy.settings.trigger_pattern || '';
            document.getElementById('policy_sub_condition').value = policy.settings.condition || 'starts_with';
            document.getElementById('policy_sub_pattern').value = policy.settings.pattern || '';
            document.getElementById('policy_sub_action').value = policy.settings.action || 'tag_all';
            document.getElementById('policy_sub_strip_pattern').checked = policy.settings.strip_pattern || false;
            
            updatePolicyEditor();
            document.getElementById('policy_editor').style.display = 'block';
            document.getElementById('policy_editor_title').textContent = 'Edit Policy: ' + policy.name;
        }

        function updatePolicyEditor() {
            var type = document.getElementById('policy_type').value;
            var defaultSettings = document.getElementById('default_policy_settings');
            var subSettings = document.getElementById('sub_policy_settings');
            
            if (type === 'default') {
                defaultSettings.style.display = 'block';
                subSettings.style.display = 'none';
            } else {
                defaultSettings.style.display = 'none';
                subSettings.style.display = 'block';
            }
        }

        function savePolicyEditor() {
            if (!currentEditingPolicyId) return;
            
            var policy = taggingPolicies.find(function(p) { return p.id === currentEditingPolicyId; });
            if (!policy) return;

            policy.name = document.getElementById('policy_name').value;
            policy.type = document.getElementById('policy_type').value;
            
            // Save settings based on type
            if (policy.type === 'default') {
                policy.settings.tag1_type = parseInt(document.getElementById('policy_default_tag1').value);
                policy.settings.tag2_type = parseInt(document.getElementById('policy_default_tag2').value);
                policy.settings.split_pattern = document.getElementById('policy_default_split').value;
                policy.settings.prefix = document.getElementById('policy_default_prefix').value;
                policy.settings.suffix = document.getElementById('policy_default_suffix').value;
            } else {
                policy.settings.trigger_type = document.getElementById('policy_sub_trigger_type').value;
                policy.settings.trigger_pattern = document.getElementById('policy_sub_trigger_pattern').value;
                policy.settings.condition = document.getElementById('policy_sub_condition').value;
                policy.settings.pattern = document.getElementById('policy_sub_pattern').value;
                policy.settings.action = document.getElementById('policy_sub_action').value;
                policy.settings.tag_type = parseInt(document.getElementById('policy_sub_tag_type').value);
                policy.settings.strip_pattern = document.getElementById('policy_sub_strip_pattern').checked;
            }

            closePolicyEditor();
            renderTaggingPolicies();
            updateMsgPreview();
        }

        function closePolicyEditor() {
            currentEditingPolicyId = null;
            document.getElementById('policy_editor').style.display = 'none';
        }

        function deleteTaggingPolicy(policyId) {
            if (!confirm('Delete this tagging policy?')) return;
            taggingPolicies = taggingPolicies.filter(function(p) { return p.id !== policyId; });
            renderTaggingPolicies();
            updateMsgPreview();
        }

        function toggleTaggingPolicy(policyId) {
            var policy = taggingPolicies.find(function(p) { return p.id === policyId; });
            if (policy) {
                policy.enabled = !policy.enabled;
                renderTaggingPolicies();
                updateMsgPreview();
            }
        }

        function moveTaggingPolicy(policyId, direction) {
            var index = taggingPolicies.findIndex(function(p) { return p.id === policyId; });
            if (index === -1) return;

            if (direction === 'up' && index > 0) {
                var temp = taggingPolicies[index];
                taggingPolicies[index] = taggingPolicies[index - 1];
                taggingPolicies[index - 1] = temp;
            } else if (direction === 'down' && index < taggingPolicies.length - 1) {
                var temp = taggingPolicies[index];
                taggingPolicies[index] = taggingPolicies[index + 1];
                taggingPolicies[index + 1] = temp;
            }
            
            renderTaggingPolicies();
            updateMsgPreview();
        }

        function renderTaggingPolicies() {
            var container = document.getElementById('tagging_policies_list');
            if (!container) return;

            container.innerHTML = '';

            if (taggingPolicies.length === 0) {
                container.innerHTML = '<div class="text-center text-gray-500 text-xs py-4 border border-gray-700 rounded">No policies defined. Click "+ Add Policy" to create your first policy.</div>';
                return;
            }

            taggingPolicies.forEach(function(policy, index) {
                var typeIcon = policy.type === 'default' ? '📍' : '⚡';
                var typeColor = policy.type === 'default' ? 'green' : 'purple';
                var typeText = policy.type === 'default' ? 'Default' : 'Sub-tagging';
                
                var description = '';
                if (policy.type === 'default') {
                    var tag1Name = RTPLUS_TYPES[policy.settings.tag1_type] ? RTPLUS_TYPES[policy.settings.tag1_type][0] : 'Unknown';
                    var tag2Name = RTPLUS_TYPES[policy.settings.tag2_type] ? RTPLUS_TYPES[policy.settings.tag2_type][0] : 'Unknown';
                    description = tag1Name + ' + ' + tag2Name + ' • Split: "' + (policy.settings.split_pattern || ' - ') + '"';
                } else {
                    var tagName = RTPLUS_TYPES[policy.settings.tag_type] ? RTPLUS_TYPES[policy.settings.tag_type][0] : 'Unknown';
                    description = tagName + ' • ' + policy.settings.condition.replace('_', ' ') + ': "' + policy.settings.pattern + '"';
                }
                
                var policyHtml = `
                    <div class="bg-[#1a1a1a] border border-${typeColor}-800/50 rounded p-3">
                        <div class="flex items-center justify-between mb-2">
                            <div class="flex items-center gap-3">
                                <label class="flex items-center gap-2 cursor-pointer">
                                    <input type="checkbox" ${policy.enabled ? 'checked' : ''} 
                                           onchange="toggleTaggingPolicy(${policy.id})"
                                           class="accent-${typeColor}-600">
                                    <div class="flex items-center gap-2">
                                        <span class="text-lg">${typeIcon}</span>
                                        <div>
                                            <div class="text-sm font-bold text-white">${escapeHtml(policy.name)}</div>
                                            <div class="text-xs text-${typeColor}-400">${typeText}</div>
                                        </div>
                                    </div>
                                </label>
                            </div>
                            <div class="flex gap-1">
                                <button onclick="moveTaggingPolicy(${policy.id}, 'up')" ${index === 0 ? 'disabled' : ''}
                                        class="w-6 h-6 text-xs bg-gray-700 hover:bg-gray-600 disabled:bg-gray-800 disabled:text-gray-600 rounded">↑</button>
                                <button onclick="moveTaggingPolicy(${policy.id}, 'down')" ${index === taggingPolicies.length - 1 ? 'disabled' : ''}
                                        class="w-6 h-6 text-xs bg-gray-700 hover:bg-gray-600 disabled:bg-gray-800 disabled:text-gray-600 rounded">↓</button>
                                <button onclick="editTaggingPolicy(${policy.id})" 
                                        class="w-6 h-6 text-xs bg-blue-800 hover:bg-blue-700 rounded text-white">✏</button>
                                <button onclick="deleteTaggingPolicy(${policy.id})" 
                                        class="w-6 h-6 text-xs bg-red-800 hover:bg-red-700 rounded text-white">×</button>
                            </div>
                        </div>
                        <div class="text-xs text-gray-400">${description}</div>
                    </div>
                `;
                container.innerHTML += policyHtml;
            });
        }

        function initializeTaggingPolicies() {
            // Initialize with example policies
            taggingPolicies = [
                {
                    id: Date.now() + 1,
                    name: 'Music Default',
                    type: 'default',
                    enabled: true,
                    settings: {
                        tag1_type: 4, // Artist
                        tag2_type: 1, // Title
                        split_pattern: ' - ',
                        prefix: '',
                        suffix: ''
                    }
                },
                {
                    id: Date.now() + 2,
                    name: 'Breaking News',
                    type: 'sub',
                    enabled: true,
                    settings: {
                        condition: 'starts_with',
                        pattern: '+++',
                        action: 'tag_after',
                        tag_type: 12, // Info.News
                        strip_pattern: true
                    }
                }
            ];
            renderTaggingPolicies();
        }

        function saveRTMessage() {
            var id = document.getElementById('rt_msg_edit_id').value;
            var msg = rtMessages.find(function(m) { return m.id === id; });

            // If msg not found, check if it's a pending new message
            if (!msg && pendingNewMessage && pendingNewMessage.id === id) {
                msg = pendingNewMessage;
                rtMessages.push(msg); // Add to array on save
                pendingNewMessage = null;
            }

            if (!msg) return;

            msg.buffer = currentMsgBuffer;
            msg.cycles = parseInt(document.getElementById('rt_msg_cycles').value) || 2;
            msg.source_type = document.querySelector('input[name="rt_msg_source"]:checked').value;

            msg.rt_plus_enabled = document.getElementById('rt_msg_rtplus_enabled').checked;

            if (msg.source_type === 'manual') {
                if (!msg.rt_plus_enabled) {
                    // Simple text mode
                    msg.content = document.getElementById('rt_msg_simple_text').value;
                    msg.prefix = '';
                    msg.suffix = '';
                    msg.split_delimiter = '';
                    msg.smart_rules = '[]';
                } else {
                    // Intelligent tagging mode
                    msg.content = document.getElementById('rt_msg_sample_text') ? document.getElementById('rt_msg_sample_text').value : 'Artist - Song Title'; // Store sample text
                    msg.prefix = document.getElementById('rt_msg_prefix') ? document.getElementById('rt_msg_prefix').value || '' : '';
                    msg.suffix = document.getElementById('rt_msg_suffix') ? document.getElementById('rt_msg_suffix').value || '' : '';
                    msg.split_delimiter = document.getElementById('rt_msg_split_pattern') ? document.getElementById('rt_msg_split_pattern').value : ' - ';
                    msg.rt_plus_tags = {
                        tag1_type: parseInt(document.getElementById('rt_msg_before_tag') ? document.getElementById('rt_msg_before_tag').value : '4') || 4,
                        tag2_type: parseInt(document.getElementById('rt_msg_after_tag') ? document.getElementById('rt_msg_after_tag').value : '1') || 1
                    };
                    msg.tagging_policies = JSON.stringify(taggingPolicies);
                    msg.case_sensitive = document.getElementById('rt_msg_case_sensitive') ? document.getElementById('rt_msg_case_sensitive').checked : false;
                    msg.whole_words = document.getElementById('rt_msg_whole_words') ? document.getElementById('rt_msg_whole_words').checked : false;
                    msg.sample_text = msg.content; // Store the same as content for manual mode
                }
                // Clear old manual fields
                msg.tag1_text = '';
                msg.middle = '';
                msg.tag2_text = '';
                msg.smart_rules = '[]'; // Clear old field
            } else if (msg.source_type === 'json') {
                // Save JSON fields with field mapping
                msg.content = document.getElementById('rt_msg_json_url').value;
                msg.json_field1 = document.getElementById('rt_msg_json_field1') ? document.getElementById('rt_msg_json_field1').value : '';
                msg.json_field2 = document.getElementById('rt_msg_json_field2') ? document.getElementById('rt_msg_json_field2').value : '';
                msg.json_hide_if_blank = document.getElementById('rt_msg_json_hide_if_blank') ? document.getElementById('rt_msg_json_hide_if_blank').checked : false;
                msg.split_delimiter = document.getElementById('rt_msg_json_delimiter') ? document.getElementById('rt_msg_json_delimiter').value : ' - ';
                msg.rt_plus_enabled = document.getElementById('rt_msg_rtplus_enabled').checked;
                msg.rt_plus_tags = {
                    tag1_type: parseInt(document.getElementById('rt_msg_json_tag1_type') ? document.getElementById('rt_msg_json_tag1_type').value : '4') || 4,
                    tag2_type: parseInt(document.getElementById('rt_msg_json_tag2_type') ? document.getElementById('rt_msg_json_tag2_type').value : '1') || 1
                };
                msg.tagging_policies = JSON.stringify(taggingPolicies);

                // Clear fields not used by JSON
                msg.prefix = '';
                msg.suffix = '';
                msg.tag1_text = '';
                msg.middle = '';
                msg.tag2_text = '';
                msg.case_sensitive = false;
                msg.whole_words = false;
            } else {
                // Save file/URL fields with intelligent tagging
                msg.content = document.getElementById('rt_msg_content').value;
                msg.prefix = document.getElementById('rt_msg_prefix') ? document.getElementById('rt_msg_prefix').value || '' : '';
                msg.suffix = document.getElementById('rt_msg_suffix') ? document.getElementById('rt_msg_suffix').value || '' : '';
                msg.rt_plus_enabled = document.getElementById('rt_msg_rtplus_enabled').checked;
                msg.split_delimiter = document.getElementById('rt_msg_split_pattern') ? document.getElementById('rt_msg_split_pattern').value : ' - ';
                msg.rt_plus_tags = {
                    tag1_type: parseInt(document.getElementById('rt_msg_before_tag') ? document.getElementById('rt_msg_before_tag').value : '4') || 4,
                    tag2_type: parseInt(document.getElementById('rt_msg_after_tag') ? document.getElementById('rt_msg_after_tag').value : '1') || 1
                };
                msg.tagging_policies = JSON.stringify(taggingPolicies);
                msg.case_sensitive = document.getElementById('rt_msg_case_sensitive') ? document.getElementById('rt_msg_case_sensitive').checked : false;
                msg.whole_words = document.getElementById('rt_msg_whole_words') ? document.getElementById('rt_msg_whole_words').checked : false;
                
                // Clear manual-only fields
                msg.tag1_text = '';
                msg.middle = '';
                msg.tag2_text = '';
                msg.smart_rules = '[]'; // Clear old field
            }

            // Save text trim flags
            var tpEl = document.getElementById('rt_msg_trim_parens'); msg.trim_parens = tpEl ? tpEl.checked : false;
            tpEl = document.getElementById('rt_msg_trim_brackets'); msg.trim_brackets = tpEl ? tpEl.checked : false;
            tpEl = document.getElementById('rt_msg_trim_semicolon'); msg.trim_at_semicolon = tpEl ? tpEl.checked : false;
            tpEl = document.getElementById('rt_msg_trim_maxlen'); msg.trim_max_len = tpEl ? tpEl.checked : false;

            closeRTMsgModal();
            renderRTMessages();
            syncRTMessages();
        }

        function toggleRTMessage(id) {
            var msg = rtMessages.find(function(m) { return m.id === id; });
            if (msg) {
                msg.enabled = !msg.enabled;
                renderRTMessages();
                syncRTMessages();
            }
        }

        function deleteRTMessage(id) {
            if (!confirm('Delete this message?')) return;
            rtMessages = rtMessages.filter(function(m) { return m.id !== id; });
            renderRTMessages();
            syncRTMessages();
        }

        // === JSON SOURCE FUNCTIONS ===
        var jsonFieldsCache = [];

        function fetchAndAnalyzeJSON() {
            var url = document.getElementById('rt_msg_json_url').value;
            if (!url) {
                alert('Please enter a JSON URL');
                return;
            }

            fetch('/fetch-json-structure', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: url })
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.ok) {
                    jsonFieldsCache = data.fields;
                    displayJSONFields(data.fields, data.sample);
                    populateJSONFieldDropdowns(data.fields, '', '', 4, 1); // Use defaults for manual URL entry
                    document.getElementById('rt_msg_json_structure').style.display = 'block';
                    document.getElementById('rt_msg_json_config').style.display = 'block';
                    updateMsgPreview();
                } else {
                    alert('Failed to fetch JSON: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(function(err) {
                alert('Error fetching JSON: ' + err.message);
            });
        }

        function populateJSONFieldDropdowns(fields, selectedField1, selectedField2, tag1Type, tag2Type) {
            var field1Select = document.getElementById('rt_msg_json_field1');
            var field2Select = document.getElementById('rt_msg_json_field2');

            var currentField1 = selectedField1 !== undefined ? selectedField1 : (field1Select ? field1Select.value : '');
            var currentField2 = selectedField2 !== undefined ? selectedField2 : (field2Select ? field2Select.value : '');

            if (field1Select) {
                field1Select.innerHTML = '<option value="">Select field...</option>';
                fields.forEach(function(field) {
                    var opt = document.createElement('option');
                    opt.value = field;
                    opt.textContent = field;
                    if (field === currentField1) opt.selected = true;
                    field1Select.appendChild(opt);
                });
            }

            if (field2Select) {
                field2Select.innerHTML = '<option value="">None (single field)</option>';
                fields.forEach(function(field) {
                    var opt = document.createElement('option');
                    opt.value = field;
                    opt.textContent = field;
                    if (field === currentField2) opt.selected = true;
                    field2Select.appendChild(opt);
                });
            }

            // Populate tag type selects with saved values or defaults
            var savedTag1Type = tag1Type !== undefined ? tag1Type : 4;
            var savedTag2Type = tag2Type !== undefined ? tag2Type : 1;
            populateRTPlusSelect(document.getElementById('rt_msg_json_tag1_type'), savedTag1Type);
            populateRTPlusSelect(document.getElementById('rt_msg_json_tag2_type'), savedTag2Type);
        }

        function displayJSONFields(fields, sample) {
            var container = document.getElementById('rt_msg_json_fields');
            var html = '';

            fields.forEach(function(field) {
                var value = sample[field] || '';
                var displayValue = String(value).substring(0, 40);
                if (String(value).length > 40) displayValue += '...';
                html += '<div class="flex justify-between gap-2">';
                html += '<span class="text-cyan-400 font-mono">' + field + '</span>';
                html += '<span class="text-gray-500 text-xs truncate flex-1">' + displayValue + '</span>';
                html += '</div>';
            });

            container.innerHTML = html;
        }
        function populateRTPlusSelect(selectElement, selectedValue) {
            if (!selectElement) {
                console.error('populateRTPlusSelect: selectElement is null');
                return;
            }
            
            console.log('populateRTPlusSelect: Starting population with selectedValue:', selectedValue);
            
            // Clear existing options
            selectElement.innerHTML = '';
            
            // Group types by category
            var categories = {};
            for (var i = 0; i <= 63; i++) {
                var info = RTPLUS_TYPES[i];
                if (!info) continue;
                
                var category = info[2] || 'system';
                if (!categories[category]) {
                    categories[category] = [];
                }
                categories[category].push({
                    code: i,
                    name: info[0],
                    description: info[1]
                });
            }
            
            console.log('populateRTPlusSelect: Categories grouped:', Object.keys(categories));
            
            // Define category display order
            var categoryOrder = ['music', 'station', 'news', 'media', 'contact', 'utility', 'location', 'system'];
            
            // Build hierarchical options
            categoryOrder.forEach(function(categoryKey) {
                var categoryInfo = RTPLUS_CATEGORIES[categoryKey];
                var items = categories[categoryKey];
                
                if (!items || items.length === 0) return;
                
                console.log('Adding category:', categoryKey, 'with', items.length, 'items');
                
                // Add category header (optgroup)
                var optgroup = document.createElement('optgroup');
                optgroup.label = categoryInfo.name;
                optgroup.style.fontWeight = 'bold';
                optgroup.style.backgroundColor = '#2a2a2a';
                optgroup.style.color = '#9ca3af';
                
                // Add items in this category
                items.forEach(function(item) {
                    var option = document.createElement('option');
                    option.value = item.code;
                    option.textContent = '  ' + item.code + ': ' + item.name;
                    option.title = item.description + ' (' + categoryInfo.name + ')';
                    option.style.paddingLeft = '16px';
                    option.style.backgroundColor = '#1f2937';
                    
                    if (selectedValue && item.code == selectedValue) {
                        option.selected = true;
                        console.log('Selected option:', item.code, item.name);
                    }
                    
                    optgroup.appendChild(option);
                });
                
                selectElement.appendChild(optgroup);
            });
            
            console.log('populateRTPlusSelect: Population complete. Total optgroups:', selectElement.children.length);
        }


        function displayJSONFields(fields, sample) {
            var container = document.getElementById('rt_msg_json_fields');
            var html = '';

            fields.forEach(function(field) {
                var value = sample[field] || '';
                var displayValue = String(value).substring(0, 40);
                if (String(value).length > 40) displayValue += '...';
                html += '<div class="flex justify-between gap-2">';
                html += '<span class="text-cyan-400 font-mono">' + field + '</span>';
                html += '<span class="text-gray-500 text-xs truncate flex-1">' + displayValue + '</span>';
                html += '</div>';
            });

            container.innerHTML = html;
        }


        function syncRTMessages() {
            socket.emit('update', { rt_messages: JSON.stringify(rtMessages) });
        }

        function initRTMsgTagSelects() {
            console.log('Initializing RT Message tag selectors with hierarchical categories...');
            
            // Manual mode selects
            var sel1 = document.getElementById('rt_msg_tag1_type');
            var sel2 = document.getElementById('rt_msg_tag2_type');
            if (sel1) { 
                populateRTPlusSelect(sel1, 4); // Default to Artist
                console.log('Populated rt_msg_tag1_type');
            }
            if (sel2) { 
                populateRTPlusSelect(sel2, 1); // Default to Title
                console.log('Populated rt_msg_tag2_type');
            }

            // JSON mode selects
            var sel1Json = document.getElementById('rt_msg_json_tag1_type');
            var sel2Json = document.getElementById('rt_msg_json_tag2_type');
            if (sel1Json) { 
                populateRTPlusSelect(sel1Json, 4); // Default to Artist
                console.log('Populated rt_msg_json_tag1_type');
            }
            if (sel2Json) { 
                populateRTPlusSelect(sel2Json, 1); // Default to Title
                console.log('Populated rt_msg_json_tag2_type');
            }

            // Auto mode selects (file/URL)
            var sel1Auto = document.getElementById('rt_msg_tag1_type_auto');
            var sel2Auto = document.getElementById('rt_msg_tag2_type_auto');
            if (sel1Auto) { 
                populateRTPlusSelect(sel1Auto, 4); // Default to Artist
                console.log('Populated rt_msg_tag1_type_auto');
            }
            if (sel2Auto) { 
                populateRTPlusSelect(sel2Auto, 1); // Default to Title
                console.log('Populated rt_msg_tag2_type_auto');
            }
            
            // Initialize intelligent tagging system
            initializeTaggingPolicies();
            console.log('Tagging policies system initialized');
            
            console.log('RT Message tag selectors initialized successfully!');
        }

        function initRTPlusBuilder() {
            console.log('Initializing RT+ Builder with hierarchical categories...');
            var select1 = document.getElementById('builder_tag1_type');
            var select2 = document.getElementById('builder_tag2_type');
            if (!select1 || !select2) {
                console.error('RT+ Builder selects not found!');
                return;
            }

            console.log('Populating Tag 1 select...');
            // Clear and populate Tag 1 selector
            populateRTPlusSelect(select1, 4);  // Default to Artist
            
            console.log('Populating Tag 2 select...');
            // Setup Tag 2 selector with "None" option
            select2.innerHTML = '';
            var noneOption = document.createElement('option');
            noneOption.value = '-1';
            noneOption.textContent = 'None (single tag)';
            noneOption.style.fontWeight = 'bold';
            noneOption.style.backgroundColor = '#333';
            select2.appendChild(noneOption);
            
            // Add hierarchical options for Tag 2
            populateRTPlusSelect(select2, 1);  // Default to Title
            
            console.log('RT+ Builder initialized successfully!');
        }

        // Toggle category reference panel
        function toggleCategoryReference() {
            var panel = document.getElementById('category_reference');
            var button = document.getElementById('toggle_categories');
            
            if (panel.classList.contains('hidden')) {
                panel.classList.remove('hidden');
                button.textContent = 'Hide Reference';
            } else {
                panel.classList.add('hidden');
                button.textContent = 'Show Reference';
            }
        }

        function openRTPlusModal() {
            initRTPlusBuilder();
            loadBuilderFromState();
            document.getElementById('rtplus_modal').style.display = 'flex';

            // Show/hide buffer tabs based on manual buffer mode
            var manualBuffers = document.getElementById('rt_manual_buffers');
            var bufferTabs = document.getElementById('builder_buffer_tabs');
            if (bufferTabs) {
                bufferTabs.style.display = (manualBuffers && manualBuffers.checked) ? 'flex' : 'none';
            }

            // Pre-populate split source from current RT text
            var rtText = document.getElementById('rt_text');
            if (rtText && rtText.value) {
                document.getElementById('split_source').value = rtText.value;
            }

            // Set mode radio based on current state
            var mode = '{{ state.rt_plus_mode }}' || 'format';
            var radios = document.querySelectorAll('input[name="rtplus_mode"]');
            radios.forEach(function(r) { r.checked = (r.value === mode); });
            updateBuilderPreview();
        }

        function closeRTPlusModal() {
            document.getElementById('rtplus_modal').style.display = 'none';
        }

        function setBuilderBuffer(buf) {
            saveBuilderToLocalState();
            currentBuilderBuffer = buf;

            document.getElementById('builder_tab_a').className = buf === 'a'
                ? 'px-4 py-2 rounded font-bold bg-[#d946ef] text-white'
                : 'px-4 py-2 rounded font-bold bg-[#333] text-gray-400 hover:bg-[#444]';
            document.getElementById('builder_tab_b').className = buf === 'b'
                ? 'px-4 py-2 rounded font-bold bg-[#d946ef] text-white'
                : 'px-4 py-2 rounded font-bold bg-[#333] text-gray-400 hover:bg-[#444]';

            loadBuilderBufferToUI();
            updateBuilderPreview();
        }

        function saveBuilderToLocalState() {
            var s = builderState[currentBuilderBuffer];
            s.prefix = document.getElementById('builder_prefix').value;
            s.tag1_type = parseInt(document.getElementById('builder_tag1_type').value);
            s.tag1_text = document.getElementById('builder_tag1_text').value;
            s.middle = document.getElementById('builder_middle').value;
            s.tag2_type = parseInt(document.getElementById('builder_tag2_type').value);
            s.tag2_text = document.getElementById('builder_tag2_text').value;
            s.suffix = document.getElementById('builder_suffix').value;
        }

        function loadBuilderBufferToUI() {
            var s = builderState[currentBuilderBuffer];
            document.getElementById('builder_prefix').value = s.prefix || '';
            document.getElementById('builder_tag1_type').value = s.tag1_type >= 0 ? s.tag1_type : 4;
            document.getElementById('builder_tag1_text').value = s.tag1_text || '';
            document.getElementById('builder_middle').value = s.middle || ' - ';
            document.getElementById('builder_tag2_type').value = s.tag2_type >= 0 ? s.tag2_type : 1;
            document.getElementById('builder_tag2_text').value = s.tag2_text || '';
            document.getElementById('builder_suffix').value = s.suffix || '';
        }

        function escapeHtml(text) {
            if (text == null || text == undefined) return '';
            var div = document.createElement('div');
            div.textContent = String(text);
            return div.innerHTML;
        }

        function updateBuilderPreview() {
            var prefix = document.getElementById('builder_prefix').value;
            var tag1Type = parseInt(document.getElementById('builder_tag1_type').value);
            var tag1Text = document.getElementById('builder_tag1_text').value;
            var middle = document.getElementById('builder_middle').value;
            var tag2Type = parseInt(document.getElementById('builder_tag2_type').value);
            var tag2Text = document.getElementById('builder_tag2_text').value;
            var suffix = document.getElementById('builder_suffix').value;

            var tag1Start = prefix.length;
            var tag1End = tag1Start + tag1Text.length;
            var tag2Start = -1, tag2End = -1;

            if (tag2Type >= 0 && tag2Text) {
                tag2Start = tag1End + middle.length;
                tag2End = tag2Start + tag2Text.length;
            }

            // Build preview with highlights
            var html = escapeHtml(prefix);
            if (tag1Text) {
                html += '<span class="rtplus-tag-1">' + escapeHtml(tag1Text) + '</span>';
            }
            if (tag2Type >= 0 && tag2Text) {
                html += escapeHtml(middle);
                html += '<span class="rtplus-tag-2">' + escapeHtml(tag2Text) + '</span>';
            }
            html += escapeHtml(suffix);

            var totalLen = prefix.length + tag1Text.length +
                (tag2Type >= 0 && tag2Text ? middle.length + tag2Text.length : 0) + suffix.length;

            var rtModeEl = document.getElementById('rt_mode');
            var limit = (rtModeEl && rtModeEl.value === '2B') ? 32 : 64;

            document.getElementById('builder_preview').innerHTML = html || '<span class="text-gray-600">Empty message</span>';

            var countEl = document.getElementById('builder_char_count');
            countEl.innerText = totalLen;
            countEl.className = totalLen > limit ? 'text-red-400' : 'text-green-400';
            document.getElementById('builder_char_limit').innerText = limit;

            // Tag info with category display
            document.getElementById('builder_tag1_info').innerText = tag1Text
                ? 'pos ' + tag1Start + ', len ' + tag1Text.length
                : 'not set';
            
            var tag1TypeInfo = '';
            if (tag1Type >= 0 && RTPLUS_TYPES[tag1Type]) {
                var typeData = RTPLUS_TYPES[tag1Type];
                var categoryData = RTPLUS_CATEGORIES[typeData[2]];
                tag1TypeInfo = typeData[0] + ' (' + (categoryData ? categoryData.name : typeData[2]) + ')';
            } else {
                tag1TypeInfo = 'None';
            }
            document.getElementById('builder_tag1_typename').innerHTML = 
                '<span title="' + (RTPLUS_TYPES[tag1Type] ? RTPLUS_TYPES[tag1Type][1] : '') + '">' + tag1TypeInfo + '</span>';

            document.getElementById('builder_tag2_info').innerText = (tag2Type >= 0 && tag2Text)
                ? 'pos ' + tag2Start + ', len ' + tag2Text.length
                : 'not set';
                
            var tag2TypeInfo = '';
            if (tag2Type >= 0 && RTPLUS_TYPES[tag2Type]) {
                var typeData = RTPLUS_TYPES[tag2Type];
                var categoryData = RTPLUS_CATEGORIES[typeData[2]];
                tag2TypeInfo = typeData[0] + ' (' + (categoryData ? categoryData.name : typeData[2]) + ')';
            } else {
                tag2TypeInfo = 'None';
            }
            document.getElementById('builder_tag2_typename').innerHTML = 
                '<span title="' + (RTPLUS_TYPES[tag2Type] ? RTPLUS_TYPES[tag2Type][1] : '') + '">' + tag2TypeInfo + '</span>';
        }

        function loadBuilderFromState() {
            // Load saved builder state from server
            try {
                var savedA = '{{ state.rt_plus_builder_a|e }}';
                var savedB = '{{ state.rt_plus_builder_b|e }}';
                if (savedA) builderState.a = JSON.parse(savedA);
                if (savedB) builderState.b = JSON.parse(savedB);
            } catch (e) {}
            // Load saved regex rules
            try {
                var savedRegA = {{ state.rt_plus_regex_rules_a|tojson }};
                var savedRegB = {{ state.rt_plus_regex_rules_b|tojson }};
                regexRules.a = JSON.parse(savedRegA) || [];
                regexRules.b = JSON.parse(savedRegB) || [];
            } catch(e) { regexRules.a = []; regexRules.b = []; }
            loadBuilderBufferToUI();
        }

        function applyBuilderConfig() {
            saveBuilderToLocalState();

            // Build RT messages from builder state
            function buildMsg(s) {
                var msg = s.prefix + s.tag1_text;
                if (s.tag2_type >= 0 && s.tag2_text) {
                    msg += s.middle + s.tag2_text;
                }
                msg += s.suffix;
                return msg;
            }

            var rtA = buildMsg(builderState.a);
            var rtB = buildMsg(builderState.b);

            // Update RT fields in UI
            var manualBuffers = document.getElementById('rt_manual_buffers');
            if (manualBuffers && manualBuffers.checked) {
                document.getElementById('rt_a').value = rtA;
                document.getElementById('rt_b').value = rtB;
            } else {
                document.getElementById('rt_text').value = rtA;
            }

            // Send builder state to backend
            socket.emit('update', {
                rt_plus_mode: 'builder',
                rt_plus_builder_a: JSON.stringify(builderState.a),
                rt_plus_builder_b: JSON.stringify(builderState.b)
            });

            // Hide legacy format fields since we're using builder mode
            var formatSingle = document.getElementById('rt_format_single');
            var formatDual = document.getElementById('rt_format_dual');
            if (formatSingle) formatSingle.style.display = 'none';
            if (formatDual) formatDual.style.display = 'none';

            sync();
            closeRTPlusModal();
        }

        function resetBuilder() {
            builderState[currentBuilderBuffer] = {
                prefix: '', tag1_type: 4, tag1_text: '',
                middle: ' - ', tag2_type: 1, tag2_text: '', suffix: ''
            };
            loadBuilderBufferToUI();
            updateBuilderPreview();
        }

        function updateRTPlusMode() {
            var mode = document.querySelector('input[name="rtplus_mode"]:checked').value;
            socket.emit('update', { rt_plus_mode: mode });

            var formatSingle = document.getElementById('rt_format_single');
            var formatDual = document.getElementById('rt_format_dual');
            var regexPanel = document.getElementById('regex_rules_panel');
            var isBuilder = (mode === 'builder');
            var isRegex   = (mode === 'regex');
            if (formatSingle) formatSingle.style.display = (isBuilder || isRegex) ? 'none' : 'block';
            if (formatDual)   formatDual.style.display   = (isBuilder || isRegex) ? 'none' : 'flex';
            if (regexPanel)   regexPanel.style.display   = isRegex ? 'block' : 'none';
            // Show buffer A/B tabs only in regex mode + manual buffer mode
            var regexBufTabs = document.getElementById('regex_buf_tabs');
            if (regexBufTabs) {
                var rtBufModeEl = document.getElementById('rt_buffer_mode');
                regexBufTabs.style.display = (isRegex && rtBufModeEl && rtBufModeEl.value === 'manual') ? 'flex' : 'none';
            }
            if (isRegex) renderRegexRules();
        }

        // ── RT Buffer Mode (Single / AUTO / Manual A/B) ───────────────────────
        function updateRTBufferMode() {
            var el = document.getElementById('rt_buffer_mode');
            if (!el) return;
            var mode = el.value;
            socket.emit('update', {
                rt_manual_buffers: (mode === 'manual'),
                rt_auto_ab:        (mode === 'auto')
            });
            // Show A/B regex buffer tabs only in manual mode
            var regexBufTabs = document.getElementById('regex_buf_tabs');
            if (regexBufTabs) {
                var modeEl = document.querySelector('input[name="rtplus_mode"]:checked');
                regexBufTabs.style.display = (mode === 'manual' && modeEl && modeEl.value === 'regex') ? 'flex' : 'none';
            }
        }

        // ── RT+ Regex Rules (legacy builder panel) ────────────────────────────
        var regexRules = { a: [], b: [] };
        var currentRegexBuffer = 'a';

        function setRegexBuffer(buf) {
            currentRegexBuffer = buf;
            ['a','b'].forEach(function(b) {
                var tab = document.getElementById('regex_tab_' + b);
                if (tab) tab.className = (b === buf)
                    ? 'px-2 py-1 rounded text-xs font-bold bg-[#d946ef] text-white'
                    : 'px-2 py-1 rounded text-xs font-bold bg-[#333] text-gray-400 hover:bg-[#444]';
            });
            renderRegexRules();
        }

        function addRegexRule() {
            regexRules[currentRegexBuffer].push({ pattern: '', tag1_type: 4, tag2_type: -1 });
            renderRegexRules();
        }

        function removeRegexRule(idx) {
            regexRules[currentRegexBuffer].splice(idx, 1);
            renderRegexRules();
            saveRegexRules();
        }

        function escHtml(s) {
            return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        }

        function buildRTPlusTypeOptions(selected) {
            var html = '';
            
            // Group types by category
            var categories = {};
            for (var i = 0; i <= 63; i++) {
                var info = RTPLUS_TYPES[i];
                if (!info) continue;
                
                var category = info[2] || 'system';
                if (!categories[category]) {
                    categories[category] = [];
                }
                categories[category].push({
                    code: i,
                    name: info[0],
                    description: info[1]
                });
            }
            
            // Define category display order
            var categoryOrder = ['music', 'station', 'news', 'media', 'contact', 'utility', 'location', 'system'];
            
            // Build hierarchical options
            categoryOrder.forEach(function(categoryKey) {
                var categoryInfo = RTPLUS_CATEGORIES[categoryKey];
                var items = categories[categoryKey];
                
                if (!items || items.length === 0) return;
                
                // Add category header (disabled option)
                html += '<option disabled style="background: #1f2937; color: #9ca3af; font-weight: bold; padding: 4px 8px;">';
                html += categoryInfo.name + '</option>';
                
                // Add items in this category
                items.forEach(function(item) {
                    var isSelected = item.code === parseInt(selected) ? ' selected' : '';
                    html += '<option value="' + item.code + '"' + isSelected + ' style="padding-left: 16px;">';
                    html += '  ' + item.code + ': ' + item.name + '</option>';
                });
            });
            
            return html;
        }

        function renderRegexRules() {
            var container = document.getElementById('regex_rules_list');
            if (!container) return;
            var rules = regexRules[currentRegexBuffer] || [];
            if (rules.length === 0) {
                container.innerHTML = '<div class="text-xs text-gray-600 text-center py-2">No rules yet. Click &quot;+ Add Rule&quot;.</div>';
                return;
            }
            container.innerHTML = '';
            rules.forEach(function(rule, idx) {
                var row = document.createElement('div');
                row.className = 'flex gap-2 items-center bg-[#1a1a1a] border border-[#333] rounded p-2';
                var buf = currentRegexBuffer;
                row.innerHTML =
                    '<span class="text-[10px] text-gray-600 w-4 shrink-0">' + (idx+1) + '</span>' +
                    '<input type="text"' +
                    ' class="flex-1 bg-[#111] border border-[#444] rounded px-2 py-1 text-xs font-mono"' +
                    ' placeholder="regex (e.g. ^(RADIO \\d+)$  or  ^(.*?) - (.+)$)"' +
                    ' value="' + escHtml(rule.pattern) + '"' +
                    ' oninput="regexRules[\'' + buf + '\'][' + idx + '].pattern=this.value; saveRegexRules();">' +
                    '<div class="flex flex-col gap-1">' +
                    '<select class="w-28 bg-[#111] border border-[#444] rounded px-1 py-0.5 text-[10px] text-orange-300"' +
                    ' title="Tag 1 type"' +
                    ' onchange="regexRules[\'' + buf + '\'][' + idx + '].tag1_type=parseInt(this.value); saveRegexRules();">' +
                    buildRTPlusTypeOptions(rule.tag1_type) + '</select>' +
                    '<select class="w-28 bg-[#111] border border-[#444] rounded px-1 py-0.5 text-[10px] text-cyan-300"' +
                    ' title="Tag 2 type (-1 = none)"' +
                    ' onchange="regexRules[\'' + buf + '\'][' + idx + '].tag2_type=parseInt(this.value); saveRegexRules();">' +
                    '<option value="-1"' + (parseInt(rule.tag2_type||'-1') === -1 ? ' selected' : '') + '>No Tag 2</option>' +
                    buildRTPlusTypeOptions(rule.tag2_type) + '</select>' +
                    '</div>' +
                    '<button onclick="removeRegexRule(' + idx + ')"' +
                    ' class="px-1.5 py-1 bg-red-900 hover:bg-red-700 rounded text-xs text-white shrink-0" title="Remove rule">\u00d7</button>';
                container.appendChild(row);
            });
        }

        function saveRegexRules() {
            socket.emit('update', {
                rt_plus_regex_rules_a: JSON.stringify(regexRules.a),
                rt_plus_regex_rules_b: JSON.stringify(regexRules.b)
            });
        }

        // ── Per-message Regex Rules (RT message modal) ────────────────────────
        var msgRegexRules = [];

        function updateRTPlusMsgMethod() {
            var el = document.querySelector('input[name="rt_msg_rtplus_method"]:checked');
            if (!el) return;
            var splitWrap = document.getElementById('rt_msg_rtplus_split_wrap');
            var regexWrap = document.getElementById('rt_msg_rtplus_regex_wrap');
            if (splitWrap) splitWrap.style.display = (el.value === 'split') ? 'block' : 'none';
            if (regexWrap) regexWrap.style.display  = (el.value === 'regex') ? 'block' : 'none';
        }

        function addMsgRegexRule() {
            msgRegexRules.push({ pattern: '', tag1_type: 4, tag2_type: -1 });
            renderMsgRegexRules();
        }

        function removeMsgRegexRule(idx) {
            msgRegexRules.splice(idx, 1);
            renderMsgRegexRules();
        }

        function renderMsgRegexRules() {
            var container = document.getElementById('rt_msg_regex_rules_list');
            if (!container) return;
            if (msgRegexRules.length === 0) {
                container.innerHTML = '<div class="text-xs text-gray-600 text-center py-2">No rules. Click &quot;+ Add Rule&quot;.</div>';
                return;
            }
            container.innerHTML = '';
            msgRegexRules.forEach(function(rule, idx) {
                var row = document.createElement('div');
                row.className = 'flex gap-2 items-center bg-[#0a0a0a] border border-[#333] rounded p-1';
                row.innerHTML =
                    '<span class="text-[10px] text-gray-600 shrink-0">' + (idx+1) + '</span>' +
                    '<input type="text"' +
                    ' class="flex-1 bg-[#111] border border-[#444] rounded px-2 py-0.5 text-xs font-mono"' +
                    ' placeholder="regex"' +
                    ' value="' + escHtml(rule.pattern) + '"' +
                    ' oninput="msgRegexRules[' + idx + '].pattern=this.value;">' +
                    '<select class="w-24 bg-[#111] border border-[#444] rounded px-1 text-[10px] text-orange-300"' +
                    ' title="Tag 1" onchange="msgRegexRules[' + idx + '].tag1_type=parseInt(this.value);">' +
                    buildRTPlusTypeOptions(rule.tag1_type) + '</select>' +
                    '<select class="w-24 bg-[#111] border border-[#444] rounded px-1 text-[10px] text-cyan-300"' +
                    ' title="Tag 2" onchange="msgRegexRules[' + idx + '].tag2_type=parseInt(this.value);">' +
                    '<option value="-1"' + (parseInt(rule.tag2_type||'-1') === -1 ? ' selected' : '') + '>No Tag 2</option>' +
                    buildRTPlusTypeOptions(rule.tag2_type) + '</select>' +
                    '<button onclick="removeMsgRegexRule(' + idx + ')"' +
                    ' class="px-1 bg-red-900 hover:bg-red-700 rounded text-xs text-white">\u00d7</button>';
                container.appendChild(row);
            });
        }

        function showSplitPanel() {
            document.getElementById('split_panel').classList.remove('hidden');
            document.getElementById('btn_show_split').classList.add('hidden');
        }

        function hideSplitPanel() {
            document.getElementById('split_panel').classList.add('hidden');
            document.getElementById('btn_show_split').classList.remove('hidden');
        }

        function performSplit() {
            var source = document.getElementById('split_source').value;
            var delimiter = document.getElementById('split_delimiter').value;

            if (!source || !delimiter) {
                alert('Please enter source text and delimiter');
                return;
            }

            var parts = source.split(delimiter);
            if (parts.length >= 2) {
                // First part goes to Tag 1, second to Tag 2
                document.getElementById('builder_tag1_text').value = parts[0].trim();
                document.getElementById('builder_tag2_text').value = parts[1].trim();
                document.getElementById('builder_middle').value = delimiter;
                document.getElementById('builder_prefix').value = '';
                // If there's a third part, put it in suffix
                if (parts.length > 2) {
                    document.getElementById('builder_suffix').value = delimiter + parts.slice(2).join(delimiter);
                } else {
                    document.getElementById('builder_suffix').value = '';
                }
            } else {
                // Only one part - put it all in Tag 1
                document.getElementById('builder_tag1_text').value = source.trim();
                document.getElementById('builder_tag2_text').value = '';
                document.getElementById('builder_tag2_type').value = '-1';
            }

            hideSplitPanel();
            updateBuilderPreview();
        }

        function setTab(id, evt) {
            document.querySelectorAll('.content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById(id).classList.add('active');
            const tgt = evt ? evt.target : (typeof event !== 'undefined' ? event.target : null);
            if (tgt) tgt.classList.add('active');
        }

        function toggleTdcMode(type) {
            // type is either '5a' or '5b'
            const mode = document.getElementById('tdc_' + type + '_mode').value;
            const textContainer = document.getElementById('tdc_' + type + '_text_container');
            const pcOptions = document.getElementById('tdc_pc_options');

            if (mode === 'pc_status') {
                textContainer.style.display = 'none';
                pcOptions.style.display = 'block';
            } else {
                textContainer.style.display = 'block';
                // Check if the other type is also in pc_status mode
                const otherType = type === '5a' ? '5b' : '5a';
                const otherMode = document.getElementById('tdc_' + otherType + '_mode').value;
                if (otherMode !== 'pc_status') {
                    pcOptions.style.display = 'none';
                }
            }
        }

        function updatePwr() {
            let btn = document.getElementById('pwrBtn');
            if(running) {
                btn.innerText = "ON AIR";
                btn.classList.add('on');
            } else {
                btn.innerText = "OFF AIR";
                btn.classList.remove('on');
            }
        }
        updatePwr();
        
        socket.on('monitor', function(data) {
            // Heartbeat animation
            let hb = document.getElementById('heartbeat');
            if (hb) hb.style.opacity = (hb.style.opacity == 0.2 ? 1 : 0.2);

            const setText = (id, v) => { const el = document.getElementById(id); if (el) el.innerText = v; };
            setText('live_ps', data.ps);
            setText('live_rt', data.rt);
            setText('live_lps', data.lps);
            setText('live_ptyn', data.ptyn);
            setText('live_pi', data.pi);

            // PTYN column - show/hide based on enabled flag
            var ptynCol = document.getElementById('live_ptyn_col');
            var lpsCol = document.getElementById('live_lps_col');
            if (ptynCol) ptynCol.style.display = data.en_ptyn ? '' : 'none';
            // Expand Long PS to fill space when PTYN is hidden
            if (lpsCol) {
                lpsCol.className = data.en_ptyn ? '' : 'col-span-2';
            }

            // PIN row - show/hide and update
            var pinRow = document.getElementById('live_pin_row');
            if (pinRow) pinRow.style.display = (data.en_pin && data.pin_str) ? '' : 'none';
            setText('live_pin', data.pin_str || '');

            // eRT row - show/hide based on en_ert
            const ertRow = document.getElementById('live_ert_row');
            if (ertRow) ertRow.style.display = data.en_ert ? '' : 'none';
            setText('live_ert', data.ert || '');
            const ertRtplusRow = document.getElementById('live_ert_rtplus_row');
            const ertRtplusInfo = data.ert_rtplus_info || '';
            if (ertRtplusRow) ertRtplusRow.style.display = (data.en_ert && ertRtplusInfo) ? '' : 'none';
            setText('live_ert_rtplus', ertRtplusInfo ? ertRtplusInfo.split(' | ').join('\n') : '');
            
            // Format RT+ tags with type names
            if (data.rt_plus_info && data.rt_plus_info !== "No RT+" && data.rt_plus_info !== "") {
                const parts = data.rt_plus_info.split(' | ');
                const formatted = parts.map(p => {
                    const match = p.match(/^(\d+):(.+)$/);
                    if (match) {
                        const typeCode = parseInt(match[1]);
                        const content = match[2];
                        const typeName = RTPLUS_TYPES[typeCode] ? RTPLUS_TYPES[typeCode][0] : 'Unknown';
                        return typeName + ': ' + content;
                    }
                    return p;
                }).join('\n');
                setText('live_rt_plus', formatted);
            } else {
                setText('live_rt_plus', data.rt_plus_info || "No RT+");
            }
            
            // Decoder flags (DI)
            const setCheck = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };
            setCheck('live_di_stereo', data.di_stereo);
            setCheck('live_di_head', data.di_head);
            setCheck('live_di_comp', data.di_comp);
            setCheck('live_di_dyn', data.di_dyn);
            
            // Status flags
            const setIndicator = (id, v) => { 
                const el = document.getElementById(id); 
                if (el) {
                    el.className = 'inline-block w-2 h-2 rounded-full ' + (v ? 'bg-green-400' : 'bg-gray-600');
                }
            };
            setIndicator('tp_indicator', data.tp);
            setIndicator('ta_indicator', data.ta);
            setIndicator('ms_indicator', data.ms);
            
            // Alternative Frequencies - format based on method
            let afDisplay = "None";
            if (data.af_list && data.af_list.trim()) {
                const methodA = data.af_list.trim();
                let methodB = "";
                try {
                    const pairs = JSON.parse(data.af_pairs || "[]");
                    if (pairs && pairs.length > 0) {
                        // Format: each pair is {main: "101.1", alts: "96.2, 97.8", regional: false}
                        const pairLines = [];
                        pairs.forEach(p => {
                            const main = p.main || "";
                            const alts = (p.alts || "").split(',').map(a => a.trim()).filter(a => a);
                            alts.forEach(alt => {
                                pairLines.push(main + " → " + alt);
                            });
                        });
                        methodB = pairLines.join("\n");
                    }
                } catch(e) {}
                
                if (data.af_method === "A") {
                    afDisplay = "A: " + methodA;
                } else if (data.af_method === "B") {
                    afDisplay = methodB ? "B: " + methodB : "None";
                } else if (data.af_method === "Both") {
                    const parts = [];
                    if (methodA) parts.push("A: " + methodA);
                    if (methodB) parts.push("B:\n" + methodB);
                    afDisplay = parts.length > 0 ? parts.join("\n\n") : "None";
                }
            }
            setText('live_af', afDisplay);
            
            // EON Networks
            if (data.eon_networks && data.eon_networks.length > 0) {
                const eonText = data.eon_networks.map(n => n.pi + ' / ' + n.ps).join('\n');
                setText('live_eon', eonText);
            } else {
                setText('live_eon', "None");
            }
            
            // RDS2 Status
            const rds2Section = document.getElementById('rds2_status_section');
            const rds2CarrierDetails = document.getElementById('rds2_carrier_details');
            const rds2LogoPreviewContainer = document.getElementById('rds2_logo_preview_container');
            
            // Hide if carrier count is 0 or not enabled
            if (data.rds2_carrier_count > 0 && data.rds2_enabled) {
                if (rds2Section) rds2Section.style.display = 'block';
                
                // Carrier status
                const frequencies = ['66.5 kHz', '71.25 kHz', '76 kHz'];
                const activeCarriers = [];
                data.rds2_carrier_levels.forEach((level, idx) => {
                    if (level > 0) activeCarriers.push(frequencies[idx]);
                });
                setText('rds2_carriers', activeCarriers.join(', '));
                setText('rds2_carrier_count', data.rds2_carrier_count + ' active');
                
                // Carrier levels
                if (rds2CarrierDetails) rds2CarrierDetails.style.display = 'grid';
                setText('rds2_c1_level', data.rds2_carrier_levels[0].toFixed(1) + '%');
                setText('rds2_c2_level', data.rds2_carrier_levels[1].toFixed(1) + '%');
                setText('rds2_c3_level', data.rds2_carrier_levels[2].toFixed(1) + '%');
                
                // Logo status
                if (data.rds2_logo_filename) {
                    setText('rds2_logo_name', '📡 ' + data.rds2_logo_filename);
                    
                    // Show logo preview
                    const logoPreview = document.getElementById('rds2_logo_preview');
                    if (logoPreview && rds2LogoPreviewContainer) {
                        logoPreview.src = '/uploads/' + data.rds2_logo_filename;
                        rds2LogoPreviewContainer.style.display = 'block';
                    }
                } else {
                    setText('rds2_logo_name', 'No logo loaded');
                    if (rds2LogoPreviewContainer) rds2LogoPreviewContainer.style.display = 'none';
                }
            } else {
                // Hide section when disabled or no carriers
                if (rds2Section) rds2Section.style.display = 'none';
            }

            // ARI Status
            const ariSection = document.getElementById('ari_status_section');
            if (data.en_ari) {
                if (ariSection) ariSection.style.display = 'block';

                // Region display
                const regionNames = {
                    "A": "A - Mecklenburg-WP, Bremen, Sachsen",
                    "B": "B - Schleswig-Holstein, Saarland",
                    "C": "C - Hamburg, Berlin, NRW",
                    "D": "D - Niedersachsen, Rheinland-Pfalz",
                    "E": "E - Thüringen, BW Süd",
                    "F": "F - Brandenburg, Hessen"
                };
                setText('ari_region_display', regionNames[data.ari_region] || data.ari_region);
                setText('ari_bk_freq_display', data.ari_bk_freq.toFixed(2) + ' Hz');

                // Announcement display
                if (data.ari_announcement) {
                    setText('ari_announcement_display', '🔴 ACTIVE');
                    document.getElementById('ari_announcement_display').style.color = '#ff4444';
                } else {
                    setText('ari_announcement_display', 'Inactive');
                    document.getElementById('ari_announcement_display').style.color = '';
                }

                // Mode display
                const modeText = data.ari_announcement_mode === 'rds_ta' ? 'Auto (RDS TA+TP)' : 'Manual';
                setText('ari_mode_display', modeText);
            } else {
                if (ariSection) ariSection.style.display = 'none';
            }

            // Use RBDS or RDS list based on mode
            const pty_list = data.rbds ? pty_list_rbds : pty_list_rds;
            setText('live_pty', pty_list[data.pty_idx] || "None");
            if (typeof data.pilot_generated !== 'undefined') setText('pilot_status', data.pilot_generated ? 'Generated' : 'Disabled (Pass-through/Genlock)');
        });

        socket.on('state_update', function(data) {
            if (data.custom_oda_list !== undefined) {
                var hiddenInput = document.getElementById('custom_oda_list');
                if (hiddenInput) {
                    hiddenInput.value = data.custom_oda_list;
                    loadCustomODAList();
                }
            }
        });

        // Legacy functions - kept for backward compatibility but now no-ops
        function updateRTVisibility() {
            // Old RT visibility toggle removed - using new message list UI
        }

        function updateCycleControls() {
            // Old cycle controls removed - using new message list UI
        }

        async function saveSettings() {
            const statusEl = document.getElementById('settings_status');
            if (statusEl) statusEl.innerText = 'Saving...';
            const payload = {
                auto_start: document.getElementById('auto_start').checked,
                user: document.getElementById('auth_user').value.trim(),
                site_name: document.getElementById('site_name').value.trim()
            };
            const passEl = document.getElementById('auth_pass');
            if (passEl && passEl.value.trim()) payload.password = passEl.value.trim();
            try {
                const res = await fetch('/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    if (statusEl) statusEl.innerText = 'Saved. Password updated if provided.';
                    if (passEl) passEl.value = '';
                } else {
                    if (statusEl) statusEl.innerText = 'Save failed (unauthorized or server error).';
                }
            } catch (e) {
                if (statusEl) statusEl.innerText = 'Save failed (network error).';
            }
        }

        async function saveUECPSettings() {
            const statusEl = document.getElementById('uecp_status');
            if (statusEl) statusEl.innerText = 'Applying...';
            const payload = {
                uecp_enabled:    document.getElementById('uecp_enabled').checked,
                uecp_port:       parseInt(document.getElementById('uecp_port').value, 10) || 4001,
                uecp_host:       document.getElementById('uecp_host').value.trim() || '0.0.0.0',
                uecp_psn:        parseInt(document.getElementById('uecp_psn').value, 10) || 0,
                uecp_dsn:        parseInt(document.getElementById('uecp_dsn').value, 10) || 0,
                uecp_ws_enabled: document.getElementById('uecp_ws_enabled').checked,
                uecp_ws_url:     document.getElementById('uecp_ws_url').value.trim() || 'ws://127.0.0.1/pacific',
            };
            try {
                const res = await fetch('/uecp_settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (statusEl) statusEl.innerText = data.status || data.error || (res.ok ? 'Saved.' : 'Error.');
            } catch (e) {
                if (statusEl) statusEl.innerText = 'Save failed (network error).';
            }
        }

        function updatePSGroupWarning() {
            var psGroupVersion = document.getElementById('ps_group_version').value;
            var warningDiv = document.getElementById('ps_0b_warning');
            if (warningDiv) {
                warningDiv.style.display = psGroupVersion === '0B' ? 'block' : 'none';
            }
        }

        function updatePTYList() {
            var isRBDS = document.getElementById('rbds').checked;
            var ptySelect = document.getElementById('pty');
            var currentValue = ptySelect.value;

            // Update option text based on RDS/RBDS
            Array.from(ptySelect.options).forEach(function(option) {
                var rdsText = option.getAttribute('data-rds');
                var rbdsText = option.getAttribute('data-rbds');
                option.textContent = isRBDS ? rbdsText : rdsText;
            });

            // Keep same value selected
            ptySelect.value = currentValue;

            // Sync the rbds checkbox state
            sync();
        }

        function sync() {
            const getVal = (id) => {
                let el = document.getElementById(id);
                if(!el) return null;
                if(el.type === 'checkbox') return el.checked;
                if(el.type === 'number' || el.type === 'range') return parseFloat(el.value);
                return el.value;
            };

            // Safely update any display fields only if the source exists
            const setValText = (valId, srcId) => {
                const valEl = document.getElementById(valId);
                const srcEl = document.getElementById(srcId);
                if (valEl && srcEl) valEl.innerText = srcEl.value;
            };
            setValText('val_rds', 'rds_level');
            setValText('val_pilot', 'pilot_level');
            setValText('val_rds2_c1', 'rds2_carrier1_level');
            setValText('val_rds2_c2', 'rds2_carrier2_level');
            setValText('val_rds2_c3', 'rds2_carrier3_level');
            setValText('val_ari_bk', 'ari_bk_level');
            setValText('val_ari_dk', 'ari_dk_level');
            const genEl = document.getElementById('genlock_offset');
            const valOffset = document.getElementById('val_offset');
            if (genEl && valOffset) valOffset.innerText = genEl.value;

            let data = {
                device_in_idx: getVal('dev_in'),
                device_out_idx: getVal('dev_out'),
                passthrough: getVal('passthrough'),
                genlock: getVal('genlock'),
                genlock_offset: getVal('genlock_offset'),
                rds_level: getVal('rds_level'),
                pilot_level: getVal('pilot_level'),
                output_channel: getVal('output_channel'),
                pi: getVal('pi'), pty: getVal('pty'), rbds: getVal('rbds'), tp: getVal('tp'), ta: getVal('ta'), ms: getVal('ms'),
                di_stereo: getVal('di_stereo'), di_head: getVal('di_head'), di_comp: getVal('di_comp'), di_dyn: getVal('di_dyn'),
                en_af: getVal('en_af'), af_list: getVal('af_list'), af_method: getVal('af_method'),
                ps_dynamic: getVal('ps_dynamic'), ps_centered: getVal('ps_centered'), ps_group_version: getVal('ps_group_version'),
                rt_text: getVal('rt_text'),
                rt_manual_buffers: (function(){ var el = document.getElementById('rt_buffer_mode'); return el ? el.value === 'manual' : getVal('rt_manual_buffers'); })(),
                rt_auto_ab:        (function(){ var el = document.getElementById('rt_buffer_mode'); return el ? el.value === 'auto'   : false; })(),
                rt_cycle_ab: getVal('rt_cycle_ab'),
                rt_a: getVal('rt_a'), rt_b: getVal('rt_b'), rt_mode: getVal('rt_mode'),
                rt_cycle: getVal('rt_cycle'), rt_centered: getVal('rt_centered'), rt_cr: getVal('rt_cr'),
                rt_disable_0d: getVal('rt_disable_0d'),
                rt_cycle_time: getVal('rt_cycle_time'),
                rt_ab_cycle_count: getVal('rt_ab_cycle_count'),
                
                // New RT+
                rt_plus_format_a: getVal('rt_plus_format_a'),
                rt_plus_format_b: getVal('rt_plus_format_b'),
                en_rt_plus: getVal('en_rt_plus'),
                
                ptyn: getVal('ptyn'), en_ptyn: getVal('en_ptyn'), ptyn_centered: getVal('ptyn_centered'),
                ecc: getVal('ecc'), lic: getVal('lic'), tz_offset: getVal('tz_offset'), en_ct: getVal('en_ct'), en_id: getVal('en_id'),
                en_pin: getVal('en_pin'), pin_day: getVal('pin_day'), pin_hour: getVal('pin_hour'), pin_minute: getVal('pin_minute'),
                ps_long_32: getVal('ps_long_32'), en_lps: getVal('en_lps'), lps_centered: getVal('lps_centered'), lps_cr: getVal('lps_cr'),
                // Enhanced RadioText (eRT)
                en_ert: getVal('en_ert'), ert_text: getVal('ert_text') || '', ert_encoding: getVal('ert_encoding'), 
                ert_group_type: getVal('ert_group_type'), en_ert_rtplus: getVal('en_ert_rtplus'),
                ert_rtplus_group_type: getVal('ert_rtplus_group_type') || 6,
                ert_source: getVal('ert_source') || 'ert',
                ert_messages: JSON.stringify(ertMessages),
                en_dab: getVal('en_dab'), dab_channel: getVal('dab_channel'),
                dab_eid: getVal('dab_eid'), dab_mode: getVal('dab_mode'), dab_es_flag: getVal('dab_es_flag'),
                dab_sid: getVal('dab_sid'), dab_variant: getVal('dab_variant'),
                en_eon: getVal('en_eon'), eon_services: getVal('eon_services'),
                custom_oda_list: getVal('custom_oda_list'),
                custom_groups: getVal('custom_groups'),
                rds_freq: getVal('rds_freq'),
                group_sequence: getVal('group_sequence'), scheduler_auto: getVal('scheduler_auto'),
                dynamic_control_enabled: getVal('dynamic_control_enabled'), dynamic_control_rules: getVal('dynamic_control_rules'),
                en_rds2: getVal('en_rds2'),
                rds2_num_carriers: getVal('rds2_num_carriers'),
                rds2_carrier1_level: getVal('rds2_carrier1_level'),
                rds2_carrier2_level: getVal('rds2_carrier2_level'),
                rds2_carrier3_level: getVal('rds2_carrier3_level'),

                // ARI (Autofahrer-Rundfunk-Information)
                en_ari: getVal('en_ari'),
                ari_region: getVal('ari_region'),
                ari_announcement: getVal('ari_announcement'),
                ari_announcement_mode: getVal('ari_announcement_mode'),
                ari_bk_level: getVal('ari_bk_level'),
                ari_dk_level: getVal('ari_dk_level'),

                // Transparent Data Channels (TDC)
                en_tdc_5a: getVal('en_tdc_5a'),
                en_tdc_5b: getVal('en_tdc_5b'),
                tdc_5a_channel: getVal('tdc_5a_channel'),
                tdc_5b_channel: getVal('tdc_5b_channel'),
                tdc_5a_text: getVal('tdc_5a_text'),
                tdc_5b_text: getVal('tdc_5b_text'),
                tdc_5a_mode: getVal('tdc_5a_mode'),
                tdc_5b_mode: getVal('tdc_5b_mode'),
                tdc_pc_show_cpu: getVal('tdc_pc_show_cpu'),
                tdc_pc_show_temp: getVal('tdc_pc_show_temp'),
                tdc_pc_show_ip: getVal('tdc_pc_show_ip'),
                tdc_pc_show_ram: getVal('tdc_pc_show_ram'),
                tdc_pc_show_uptime: getVal('tdc_pc_show_uptime'),

                // Web Interface
                http_port: getVal('http_port')
            };
            socket.emit('update', data);

            // Show save button when changes are made (skip on initial load)
            if (typeof window.syncCount === 'undefined') {
                window.syncCount = 0;
            }
            window.syncCount++;

            const saveBtn = document.getElementById('saveBtn');
            if (saveBtn && window.syncCount > 1) {
                saveBtn.classList.add('has-changes');
            }
        }

        // === RDS2 FUNCTIONS ===
        function updateRDS2Visibility() {
            const numCarriers = parseInt(document.getElementById('rds2_num_carriers')?.value || 0);
            const c1 = document.getElementById('rds2_carrier1_div');
            const c2 = document.getElementById('rds2_carrier2_div');
            const c3 = document.getElementById('rds2_carrier3_div');

            if (c1) c1.style.display = numCarriers >= 1 ? 'block' : 'none';
            if (c2) c2.style.display = numCarriers >= 2 ? 'block' : 'none';
            if (c3) c3.style.display = numCarriers >= 3 ? 'block' : 'none';
        }

        function updateARIAnnouncementUI() {
            const mode = document.getElementById('ari_announcement_mode')?.value || 'manual';
            const manualDiv = document.getElementById('ari_manual_announcement');
            const autoDiv = document.getElementById('ari_auto_announcement');

            if (manualDiv) manualDiv.style.display = mode === 'manual' ? 'block' : 'none';
            if (autoDiv) autoDiv.style.display = mode === 'rds_ta' ? 'block' : 'none';
        }

        function uploadRDS2Logo() {
            const fileInput = document.getElementById('rds2_logo_input');
            const statusDiv = document.getElementById('rds2_upload_status');
            const messageDiv = document.getElementById('rds2_upload_message');
            
            if (!fileInput.files || fileInput.files.length === 0) {
                showRDS2Status('Please select a file first', 'error');
                return;
            }
            
            const file = fileInput.files[0];
            const maxSize = 200 * 1024; // 200 KB
            
            if (file.size > maxSize) {
                showRDS2Status('File too large! Maximum size is 200 KB', 'error');
                return;
            }
            
            const formData = new FormData();
            formData.append('logo', file);
            
            showRDS2Status('Uploading...', 'info');
            
            fetch('/rds2/upload_logo', {
                method: 'POST',
                body: formData
            })
            .then(function(res) { return res.json(); })
            .then(function(data) {
                if (data.ok) {
                    showRDS2Status('✓ Logo uploaded successfully!', 'success');
                    setTimeout(function() { location.reload(); }, 1500);
                } else {
                    showRDS2Status('✗ Upload failed: ' + (data.error || 'Unknown error'), 'error');
                }
            })
            .catch(function(err) {
                showRDS2Status('✗ Upload failed: ' + err.message, 'error');
            });
        }

        function clearRDS2Logo() {
            if (!confirm('Remove the uploaded station logo?')) return;
            
            fetch('/rds2/clear_logo', { method: 'POST' })
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (data.ok) {
                        location.reload();
                    } else {
                        alert('Failed to clear logo');
                    }
                })
                .catch(function(err) {
                    alert('Error: ' + err.message);
                });
        }

        function showRDS2Status(message, type) {
            const statusDiv = document.getElementById('rds2_upload_status');
            const messageDiv = document.getElementById('rds2_upload_message');
            
            if (!statusDiv || !messageDiv) return;
            
            statusDiv.className = 'mt-3';
            
            if (type === 'success') {
                statusDiv.className += ' bg-green-900/20 border border-green-500/30 rounded p-3';
                messageDiv.className = 'text-sm text-green-300';
            } else if (type === 'error') {
                statusDiv.className += ' bg-red-900/20 border border-red-500/30 rounded p-3';
                messageDiv.className = 'text-sm text-red-300';
            } else {
                statusDiv.className += ' bg-blue-900/20 border border-blue-500/30 rounded p-3';
                messageDiv.className = 'text-sm text-blue-300';
            }
            
            messageDiv.textContent = message;
            statusDiv.classList.remove('hidden');
            
            if (type !== 'info') {
                setTimeout(function() {
                    statusDiv.classList.add('hidden');
                }, 5000);
            }
        }

        // === DATASET FUNCTIONS ===
        var datasets = {};
        var currentDataset = 1;

        function loadDatasets() {
            fetch('/datasets')
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    datasets = data.datasets;
                    currentDataset = data.current;
                    renderDatasetButtons();
                })
                .catch(function(e) { console.error('Failed to load datasets:', e); });
        }

        function renderDatasetButtons() {
            var container = document.getElementById('dataset_buttons');
            if (!container) return;
            container.innerHTML = '';

            for (var num in datasets) {
                var dataset = datasets[num];
                var btn = document.createElement('button');
                btn.className = parseInt(num) === currentDataset ? 'px-4 py-3 rounded text-sm font-semibold bg-pink-600 text-white' : 'px-4 py-3 rounded text-sm font-semibold bg-gray-700 hover:bg-gray-600 text-gray-200';
                btn.textContent = dataset.name || 'Dataset ' + num;
                btn.onclick = (function(n) { return function() { switchDataset(n); }; })(parseInt(num));
                container.appendChild(btn);
            }

            var nameEl = document.getElementById('current_dataset_name');
            if (nameEl && datasets[currentDataset]) {
                nameEl.textContent = datasets[currentDataset].name || 'Dataset ' + currentDataset;
            }
        }

        function switchDataset(num) {
            fetch('/datasets/' + num + '/switch', { method: 'POST' })
                .then(function(res) {
                    if (res.ok) {
                        location.reload();
                    }
                })
                .catch(function(e) { alert('Failed to switch dataset'); });
        }

        function createDataset() {
            var keys = Object.keys(datasets).map(function(n) { return parseInt(n); });
            var nextNum = keys.length > 0 ? Math.max.apply(Math, keys) + 1 : 1;
            var name = prompt('Enter dataset name:', 'Dataset ' + nextNum);
            if (!name) return;

            // Copy RDS volume and soundcard from current dataset
            var newState = {};
            if (datasets[currentDataset] && datasets[currentDataset].state) {
                var currentState = datasets[currentDataset].state;
                if (currentState.rds_level !== undefined) {
                    newState.rds_level = currentState.rds_level;
                }
                if (currentState.device_out_idx !== undefined) {
                    newState.device_out_idx = currentState.device_out_idx;
                }
            }

            fetch('/datasets/' + nextNum, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, state: newState })
            })
                .then(function(res) {
                    if (res.ok) {
                        loadDatasets();
                    }
                })
                .catch(function(e) { alert('Failed to create dataset'); });
        }

        function renameCurrentDataset() {
            // Ensure dataset exists before renaming
            if (!datasets[currentDataset]) {
                alert('Dataset not loaded. Please refresh the page.');
                return;
            }

            var currentName = datasets[currentDataset].name || 'Dataset ' + currentDataset;
            var newName = prompt('Enter new name:', currentName);
            if (!newName) return;

            datasets[currentDataset].name = newName;
            fetch('/datasets/' + currentDataset, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(datasets[currentDataset])
            })
                .then(function(res) {
                    if (res.ok) {
                        loadDatasets();
                    }
                })
                .catch(function(e) { alert('Failed to rename dataset'); });
        }

        function deleteCurrentDataset() {
            if (Object.keys(datasets).length <= 1) {
                alert('Cannot delete the last dataset');
                return;
            }
            var currentName = datasets[currentDataset] ? datasets[currentDataset].name : 'Dataset ' + currentDataset;
            if (!confirm('Delete dataset "' + currentName + '"?')) return;

            fetch('/datasets/' + currentDataset, { method: 'DELETE' })
                .then(function(res) {
                    if (res.ok) {
                        location.reload();
                    }
                })
                .catch(function(e) { alert('Failed to delete dataset'); });
        }

        // === EON FUNCTIONS ===
        var eonServices = [];

        function loadEONServices() {
            var getVal = function(id) {
                var el = document.getElementById(id);
                if (!el) return null;
                if (el.type === 'checkbox') return el.checked;
                return el.value;
            };
            try {
                eonServices = JSON.parse(getVal('eon_services') || '[]');
            } catch (e) {
                eonServices = [];
            }
            updateEONDisplay();
        }

        function updateEONDisplay() {
            var display = document.getElementById('eon_services_display');
            if (!display) return;

            if (eonServices.length === 0) {
                display.innerHTML = '<div class="text-xs text-gray-400">No services configured</div>';
            } else {
                var html = '<div class="text-xs space-y-1">';
                for (var i = 0; i < eonServices.length; i++) {
                    var svc = eonServices[i];
                    html += '<div class="p-2 bg-gray-800 hover:bg-gray-700 rounded cursor-pointer border border-gray-700 hover:border-pink-600 transition-colors" onclick="openEONModalAndEdit(' + i + ')">';
                    
                    // Line 1: PI code
                    html += '<div class="font-mono text-pink-400">' + (svc.pi_on || 'C000') + '</div>';
                    
                    // Line 2: PS name
                    html += '<div class="text-gray-200">' + (svc.ps || '        ') + '</div>';
                    
                    // Line 3: AF info (A: method A list, B: mapped frequencies)
                    var afInfo = [];
                    
                    // A: Method A AF list
                    if (svc.af_list && svc.af_list.trim()) {
                        afInfo.push('A: ' + svc.af_list.trim());
                    }
                    
                    // B: Mapped frequencies
                    if (svc.mapped_freqs && svc.mapped_freqs.length > 0) {
                        var mappedStr = svc.mapped_freqs.map(function(pair) {
                            return pair.tuned + ' → ' + pair.other;
                        }).join(', ');
                        afInfo.push('B: ' + mappedStr);
                    }
                    
                    if (afInfo.length > 0) {
                        html += '<div class="text-xs text-gray-500 break-words">' + afInfo.join(' | ') + '</div>';
                    }
                    
                    html += '</div>';
                }
                html += '</div>';
                display.innerHTML = html;
            }
        }

        function openEONModal() {
            loadEONServices();
            renderEONServiceList();
            document.getElementById('eon_modal').style.display = 'flex';
            document.getElementById('eon_edit_form').style.display = 'none';
        }

        function closeEONModal() {
            document.getElementById('eon_modal').style.display = 'none';
            document.getElementById('eon_edit_form').style.display = 'none';
        }

        function renderEONServiceList() {
            var container = document.getElementById('eon_service_list');
            container.innerHTML = '';

            if (eonServices.length === 0) {
                container.innerHTML = '<div class="text-xs text-gray-400 text-center py-4">No services configured. Click Add Service to create one.</div>';
                return;
            }

            var dragSrcIdx = null;

            for (var i = 0; i < eonServices.length; i++) {
                var svc = eonServices[i];
                var card = document.createElement('div');
                card.className = 'bg-black border border-gray-700 rounded p-3 flex justify-between items-center';
                card.draggable = true;
                card.dataset.index = i;

                // Drag events
                card.addEventListener('dragstart', (function(idx) { return function(e) {
                    dragSrcIdx = idx;
                    e.dataTransfer.effectAllowed = 'move';
                    this.style.opacity = '0.4';
                }; })(i));
                card.addEventListener('dragend', function() { this.style.opacity = ''; });
                card.addEventListener('dragover', function(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; });
                card.addEventListener('drop', (function(idx) { return function(e) {
                    e.preventDefault();
                    if (dragSrcIdx === null || dragSrcIdx === idx) return;
                    var moved = eonServices.splice(dragSrcIdx, 1)[0];
                    eonServices.splice(idx, 0, moved);
                    syncEONServices();
                    renderEONServiceList();
                    updateEONDisplay();
                    dragSrcIdx = null;
                }; })(i));

                var info = document.createElement('div');
                info.className = 'flex-1';
                
                // Build AF info string
                var afParts = [];
                if (svc.af_list && svc.af_list.trim()) {
                    afParts.push('A: ' + svc.af_list.trim());
                }
                if (svc.mapped_freqs && svc.mapped_freqs.length > 0) {
                    var mappedStr = svc.mapped_freqs.map(function(pair) {
                        return pair.tuned + ' → ' + pair.other;
                    }).join(', ');
                    afParts.push('B: ' + mappedStr);
                }
                var afDisplay = afParts.length > 0 ? afParts.join(' | ') : 'No AFs';
                
                var tpBadge = svc.tp ? '<span class="text-[10px] bg-green-600 text-white px-1 rounded ml-2">TP</span>' : '';

                info.innerHTML = '<div class="font-mono text-sm text-pink-400">' + (svc.pi_on || 'C000') + tpBadge + '</div>' +
                                '<div class="text-sm">' + (svc.ps || '        ') + '</div>' +
                                '<div class="text-xs text-gray-400 break-words">' + afDisplay + '</div>';

                var actions = document.createElement('div');
                actions.className = 'flex gap-2 items-center';

                var editBtn = document.createElement('button');
                editBtn.textContent = 'Edit';
                editBtn.className = 'px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-xs';
                editBtn.onclick = (function(idx) { return function() { editEONService(idx); }; })(i);

                var delBtn = document.createElement('button');
                delBtn.textContent = 'Delete';
                delBtn.className = 'px-3 py-1 bg-red-600 hover:bg-red-500 rounded text-xs';
                delBtn.onclick = (function(idx) { return function() { deleteEONService(idx); }; })(i);

                actions.appendChild(editBtn);
                actions.appendChild(delBtn);
                card.appendChild(info);
                card.appendChild(actions);
                container.appendChild(card);
            }
        }

        function addEONService() {
            document.getElementById('eon_edit_idx').value = '';
            document.getElementById('eon_pi').value = '';
            document.getElementById('eon_ps').value = '';
            document.getElementById('eon_pty').value = '0';
            document.getElementById('eon_af').value = '';
            document.getElementById('eon_mapped').value = '';
            document.getElementById('eon_tp').checked = false;
            document.getElementById('eon_uecp_psn').value = '0';
            document.getElementById('eon_modal_title').textContent = 'Add EON Service';
            document.getElementById('eon_edit_form').style.display = 'block';
        }

        function editEONService(idx) {
            var svc = eonServices[idx];
            document.getElementById('eon_edit_idx').value = idx;
            document.getElementById('eon_pi').value = svc.pi_on || '';
            document.getElementById('eon_ps').value = svc.ps || '';
            document.getElementById('eon_pty').value = svc.pty || 0;
            document.getElementById('eon_af').value = svc.af_list || '';
            
            // Convert mapped_freqs array to text format (comma-separated)
            var mappedText = '';
            if (svc.mapped_freqs && svc.mapped_freqs.length > 0) {
                mappedText = svc.mapped_freqs.map(function(pair) {
                    return pair.tuned + ', ' + pair.other;
                }).join('\n');
            }
            document.getElementById('eon_mapped').value = mappedText;

            document.getElementById('eon_tp').checked = svc.tp || false;
            document.getElementById('eon_uecp_psn').value = svc.uecp_psn || 0;
            document.getElementById('eon_modal_title').textContent = 'Edit EON Service';
            document.getElementById('eon_edit_form').style.display = 'block';
        }

        function deleteEONService(idx) {
            if (!confirm('Delete this EON service?')) return;
            eonServices.splice(idx, 1);
            syncEONServices();
            renderEONServiceList();
            updateEONDisplay();
        }

        function cancelEONEdit() {
            document.getElementById('eon_edit_form').style.display = 'none';
            document.getElementById('eon_modal_title').textContent = 'Manage EON Services';
        }

        function saveEONService() {
            var idx = document.getElementById('eon_edit_idx').value;
            
            // Parse mapped frequencies from text format (one pair per line, comma-separated)
            var mappedText = document.getElementById('eon_mapped').value.trim();
            var mappedFreqs = [];
            if (mappedText) {
                var lines = mappedText.split('\n');
                for (var i = 0; i < lines.length && i < 4; i++) {
                    var line = lines[i].trim();
                    if (line) {
                        var parts = line.split(',').map(function(s) { return s.trim(); });
                        if (parts.length === 2) {
                            mappedFreqs.push({ tuned: parts[0], other: parts[1] });
                        }
                    }
                }
            }
            
            var svc = {
                pi_on: document.getElementById('eon_pi').value.toUpperCase() || 'C000',
                ps: document.getElementById('eon_ps').value || '        ',
                pty: parseInt(document.getElementById('eon_pty').value) || 0,
                af_list: document.getElementById('eon_af').value || '',
                mapped_freqs: mappedFreqs,
                tp: document.getElementById('eon_tp').checked ? 1 : 0,
                uecp_psn: parseInt(document.getElementById('eon_uecp_psn').value) || 0
            };

            if (idx === '') {
                eonServices.push(svc);
            } else {
                eonServices[parseInt(idx)] = svc;
            }

            syncEONServices();
            renderEONServiceList();
            updateEONDisplay();
            cancelEONEdit();
        }

        function syncEONServices() {
            var hiddenInput = document.getElementById('eon_services');
            if (hiddenInput) {
                hiddenInput.value = JSON.stringify(eonServices);
            }
            socket.emit('update', { eon_services: JSON.stringify(eonServices) });
        }

        function openEONModalAndEdit(idx) {
            openEONModal();
            setTimeout(function() {
                editEONService(idx);
            }, 100);
        }

        // Dynamic Control Management
        var dynamicControlRules = [];

        function loadDynamicControlRules() {
            try {
                var hiddenInput = document.getElementById('dynamic_control_rules');
                if (hiddenInput && hiddenInput.value) {
                    dynamicControlRules = JSON.parse(hiddenInput.value);
                }
            } catch (e) {
                dynamicControlRules = [];
            }
        }

        function openDynamicControlModal() {
            loadDynamicControlRules();
            renderDynamicControlRuleList();
            document.getElementById('dynamic_control_modal').style.display = 'flex';
            document.getElementById('dynamic_control_edit_form').style.display = 'none';
        }

        function closeDynamicControlModal() {
            document.getElementById('dynamic_control_modal').style.display = 'none';
            document.getElementById('dynamic_control_edit_form').style.display = 'none';
        }

        function switchDCTab(tab) {
            var smartTab = document.getElementById('dc_tab_smart');
            var advancedTab = document.getElementById('dc_tab_advanced');
            var smartContent = document.getElementById('dc_smart_tab_content');
            var advancedContent = document.getElementById('dc_advanced_tab_content');

            if (tab === 'smart') {
                // Style active smart tab
                smartTab.className = 'px-4 py-2 text-sm font-bold border-b-2 border-pink-500 text-pink-400';
                advancedTab.className = 'px-4 py-2 text-sm text-gray-400 hover:text-gray-200';
                smartContent.style.display = 'block';
                advancedContent.style.display = 'none';
            } else {
                // Style active advanced tab
                smartTab.className = 'px-4 py-2 text-sm text-gray-400 hover:text-gray-200';
                advancedTab.className = 'px-4 py-2 text-sm font-bold border-b-2 border-pink-500 text-pink-400';
                smartContent.style.display = 'none';
                advancedContent.style.display = 'block';
            }
        }

        var smartActions = [];

        function addSmartAction() {
            var actionDiv = document.createElement('div');
            actionDiv.className = 'bg-gray-900 border border-gray-700 rounded p-2 space-y-2';

            // Row 1: Parameter and source type
            var row1 = document.createElement('div');
            row1.className = 'flex gap-2 items-center';

            var paramSelect = document.createElement('select');
            paramSelect.className = 'flex-1 bg-black border border-gray-600 rounded px-2 py-1 text-xs';
            paramSelect.innerHTML = `
                <option value="">Select RDS Parameter...</option>
                <option value="ms">MS (Music/Speech)</option>
                <option value="pty">PTY (Programme Type)</option>
                <option value="ptyn">PTYN (Programme Type Name)</option>
                <option value="tp">TP (Traffic Programme)</option>
                <option value="ta">TA (Traffic Announcement)</option>
                <option value="ari_announcement">ARI Announcement (DK)</option>
                <option value="pi">PI (Programme Identification)</option>
                <option value="rt_text">RT Text (RadioText)</option>
                <option value="rt_a">RT Buffer A</option>
                <option value="rt_b">RT Buffer B</option>
                <option value="en_pin">PIN Enable</option>
                <option value="pin_day">PIN Day</option>
                <option value="pin_hour">PIN Hour</option>
                <option value="pin_minute">PIN Minute</option>
                <option value="tdc_5a_text">TDC 5A Text</option>
                <option value="tdc_5b_text">TDC 5B Text</option>
                <option value="en_tdc_5a">TDC 5A Enable</option>
                <option value="en_tdc_5b">TDC 5B Enable</option>
            `;

            var sourceToggle = document.createElement('div');
            sourceToggle.className = 'flex gap-2 text-xs';
            sourceToggle.innerHTML = `
                <label class="flex items-center gap-1 cursor-pointer">
                    <input type="radio" name="source_${Date.now()}" value="json" checked class="accent-pink-500">
                    <span>JSON Field</span>
                </label>
                <label class="flex items-center gap-1 cursor-pointer">
                    <input type="radio" name="source_${Date.now()}" value="static" class="accent-pink-500">
                    <span>Static</span>
                </label>
            `;

            var deleteBtn = document.createElement('button');
            deleteBtn.className = 'bg-red-600 hover:bg-red-500 text-white rounded px-2 py-1 text-xs';
            deleteBtn.textContent = '✕';
            deleteBtn.onclick = function() {
                actionDiv.remove();
                if (document.getElementById('dc_smart_actions_list').children.length === 0) {
                    document.getElementById('dc_smart_actions_list').innerHTML = '<div class="text-xs text-gray-500 text-center py-2">No actions yet. Click "+ Add Action" to add one.</div>';
                }
            };

            row1.appendChild(paramSelect);
            row1.appendChild(sourceToggle);
            row1.appendChild(deleteBtn);

            // Row 2: Value input (dynamic)
            var valueContainer = document.createElement('div');
            valueContainer.className = 'value-container';

            // Initial JSON field input
            var jsonInput = document.createElement('input');
            jsonInput.type = 'text';
            jsonInput.className = 'w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs font-mono text-cyan-400';
            jsonInput.placeholder = 'JSON field path (e.g., data.pty)';
            valueContainer.appendChild(jsonInput);

            // Function to update value input based on source and parameter
            function updateValueInput() {
                var param = paramSelect.value;
                var isJson = sourceToggle.querySelector('input[value="json"]').checked;
                valueContainer.innerHTML = '';

                if (isJson) {
                    // JSON field input
                    var input = document.createElement('input');
                    input.type = 'text';
                    input.className = 'w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs font-mono text-cyan-400';
                    input.placeholder = 'JSON field path (e.g., data.pty, event.start_hour)';
                    valueContainer.appendChild(input);
                } else {
                    // Static value input - type depends on parameter
                    if (param === 'ms') {
                        var select = document.createElement('select');
                        select.className = 'w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                        select.innerHTML = '<option value="0">Speech</option><option value="1">Music</option>';
                        valueContainer.appendChild(select);
                    } else if (param === 'pty') {
                        var select = document.createElement('select');
                        select.className = 'w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                        select.innerHTML = `
                            <option value="0">None</option><option value="1">News</option><option value="2">Current Affairs</option>
                            <option value="3">Information</option><option value="4">Sport</option><option value="5">Education</option>
                            <option value="6">Drama</option><option value="7">Culture</option><option value="8">Science</option>
                            <option value="9">Varied</option><option value="10">Pop Music</option><option value="11">Rock Music</option>
                            <option value="12">Easy Listening</option><option value="13">Light Classical</option><option value="14">Serious Classical</option>
                            <option value="15">Other Music</option><option value="16">Weather</option><option value="17">Finance</option>
                            <option value="18">Children's</option><option value="19">Social Affairs</option><option value="20">Religion</option>
                            <option value="21">Phone-In</option><option value="22">Travel</option><option value="23">Leisure</option>
                            <option value="24">Jazz Music</option><option value="25">Country Music</option><option value="26">National Music</option>
                            <option value="27">Oldies Music</option><option value="28">Folk Music</option><option value="29">Documentary</option>
                            <option value="30">Alarm Test</option><option value="31">Alarm</option>
                        `;
                        valueContainer.appendChild(select);
                    } else if (param === 'tp' || param === 'ta' || param === 'en_pin' || param === 'en_tdc_5a' || param === 'en_tdc_5b') {
                        var select = document.createElement('select');
                        select.className = 'w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                        select.innerHTML = '<option value="0">Off (0)</option><option value="1">On (1)</option>';
                        valueContainer.appendChild(select);
                    } else if (param === 'pin_hour') {
                        var input = document.createElement('input');
                        input.type = 'number';
                        input.min = '0';
                        input.max = '23';
                        input.className = 'w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                        input.placeholder = 'Hour (0-23)';
                        valueContainer.appendChild(input);
                    } else if (param === 'pin_minute') {
                        var input = document.createElement('input');
                        input.type = 'number';
                        input.min = '0';
                        input.max = '59';
                        input.className = 'w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                        input.placeholder = 'Minute (0-59)';
                        valueContainer.appendChild(input);
                    } else if (param === 'pin_day') {
                        var input = document.createElement('input');
                        input.type = 'number';
                        input.className = 'w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                        input.placeholder = 'MJD Day';
                        valueContainer.appendChild(input);
                    } else if (param === 'ari_announcement') {
                        var select = document.createElement('select');
                        select.className = 'w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                        select.innerHTML = `
                            <option value="0">None</option><option value="1">Regional traffic</option>
                            <option value="2">Local traffic</option><option value="3">Stop+go</option>
                            <option value="4">Accident</option><option value="5">Weather</option>
                        `;
                        valueContainer.appendChild(select);
                    } else {
                        var input = document.createElement('input');
                        input.type = 'text';
                        input.className = 'w-full bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                        input.placeholder = 'Value';
                        valueContainer.appendChild(input);
                    }
                }
            }

            // Update value input when parameter or source changes
            paramSelect.onchange = updateValueInput;
            sourceToggle.querySelectorAll('input[type="radio"]').forEach(function(radio) {
                radio.onchange = updateValueInput;
            });

            actionDiv.appendChild(row1);
            actionDiv.appendChild(valueContainer);

            var list = document.getElementById('dc_smart_actions_list');
            if (list.children.length === 1 && list.children[0].textContent.includes('No actions yet')) {
                list.innerHTML = '';
            }
            list.appendChild(actionDiv);
        }

        function saveSmartRule() {
            var url = document.getElementById('dc_smart_url').value.trim();
            var field = document.getElementById('dc_smart_field').value.trim();
            var keywords = document.getElementById('dc_smart_keywords').value.trim();

            if (!url) {
                alert('Please enter a JSON URL');
                return;
            }
            if (!field) {
                alert('Please enter a JSON field to check');
                return;
            }
            if (!keywords) {
                alert('Please enter keywords to detect');
                return;
            }

            var actionsList = document.getElementById('dc_smart_actions_list');
            var actions = [];

            for (var i = 0; i < actionsList.children.length; i++) {
                var actionDiv = actionsList.children[i];
                if (actionDiv.children.length < 2) continue;

                var param = actionDiv.children[0].value;
                var value = actionDiv.children[1].value.trim();

                if (param && value !== '') {
                    actions.push({param: param, value: value});
                }
            }

            if (actions.length === 0) {
                alert('Please add at least one action');
                return;
            }

            // Create a multi_action rule
            var rule = {
                name: 'Smart: ' + keywords.split(',')[0].trim(),
                enabled: true,
                url: url,
                poll_interval: 2,  // Default 2 second polling
                field_path: field,
                rds_param: actions[0].param,  // Use first action as primary param for display
                mapping_type: 'multi_action',
                keywords: keywords,
                actions: actions
            };

            dynamicControlRules.push(rule);
            updateDataset('dynamic_control_rules', dynamicControlRules);
            renderDynamicControlRuleList();

            // Clear form
            document.getElementById('dc_smart_url').value = '';
            document.getElementById('dc_smart_field').value = '';
            document.getElementById('dc_smart_keywords').value = '';
            document.getElementById('dc_smart_actions_list').innerHTML = '<div class="text-xs text-gray-500 text-center py-2">No actions yet. Click "+ Add Action" to add one.</div>';

            alert('✓ Smart Rule saved successfully!');
        }

        function cancelSmartRule() {
            document.getElementById('dc_smart_url').value = '';
            document.getElementById('dc_smart_field').value = '';
            document.getElementById('dc_smart_keywords').value = '';
            document.getElementById('dc_smart_actions_list').innerHTML = '<div class="text-xs text-gray-500 text-center py-2">No actions yet. Click "+ Add Action" to add one.</div>';
        }

        function testSmartRuleURL() {
            var url = document.getElementById('dc_smart_url').value.trim();
            if (!url) {
                alert('Please enter a JSON URL first');
                return;
            }

            var browser = document.getElementById('dc_smart_json_browser');
            var tree = document.getElementById('dc_smart_json_tree');
            tree.innerHTML = '<div class="text-gray-400">🔄 Fetching JSON...</div>';
            browser.style.display = 'block';

            // Use backend proxy to avoid CORS issues
            fetch('/dynamic_control/fetch_json', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url: url})
            })
                .then(function(response) {
                    return response.json().then(function(data) {
                        if (!response.ok) {
                            throw new Error(data.error || 'HTTP ' + response.status);
                        }
                        return data;
                    });
                })
                .then(function(result) {
                    if (result.success && result.data) {
                        renderSmartRuleJSONTree(result.data, tree);
                    } else {
                        throw new Error(result.error || 'Unknown error');
                    }
                })
                .catch(function(error) {
                    tree.innerHTML = '<div class="text-red-400">✗ ' + error.message + '</div>' +
                                    '<div class="text-[10px] text-gray-500 mt-2">Tips: Check URL is correct and accessible</div>';
                });
        }

        function renderSmartRuleJSONTree(obj, container) {
            container.innerHTML = '';

            function renderNode(value, key, currentPath, parentDiv) {
                var fullPath = currentPath ? currentPath + '.' + key : key;
                var div = document.createElement('div');
                div.className = 'py-0.5';

                if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
                    // Object node
                    div.innerHTML = '<span class="text-blue-400 cursor-pointer hover:underline" onclick="selectSmartRuleField(\'' + fullPath + '\')">' +
                                   key + '</span> <span class="text-gray-500">{...}</span>';
                    parentDiv.appendChild(div);

                    var nested = document.createElement('div');
                    nested.className = 'ml-4';
                    for (var k in value) {
                        renderNode(value[k], k, fullPath, nested);
                    }
                    parentDiv.appendChild(nested);
                } else if (Array.isArray(value)) {
                    // Array node
                    div.innerHTML = '<span class="text-purple-400 cursor-pointer hover:underline" onclick="selectSmartRuleField(\'' + fullPath + '\')">' +
                                   key + '</span> <span class="text-gray-500">[' + value.length + ']</span>';
                    parentDiv.appendChild(div);
                    if (value.length > 0 && typeof value[0] === 'object') {
                        var nested = document.createElement('div');
                        nested.className = 'ml-4';
                        renderNode(value[0], '[0]', fullPath, nested);
                        parentDiv.appendChild(nested);
                    }
                } else {
                    // Leaf node (clickable)
                    var valueStr = String(value);
                    if (valueStr.length > 40) valueStr = valueStr.substring(0, 40) + '...';
                    div.innerHTML = '<span class="text-cyan-400 cursor-pointer hover:underline hover:text-cyan-300" onclick="selectSmartRuleField(\'' +
                                   fullPath + '\')">' + key + '</span> <span class="text-gray-500">= ' +
                                   '<span class="text-yellow-300">' + valueStr + '</span></span>';
                    parentDiv.appendChild(div);
                }
            }

            for (var key in obj) {
                renderNode(obj[key], key, '', container);
            }
        }

        function selectSmartRuleField(path) {
            document.getElementById('dc_smart_field').value = path;
        }

        function closeSmartRuleBrowser() {
            document.getElementById('dc_smart_json_browser').style.display = 'none';
        }

        function renderDynamicControlRuleList() {
            var container = document.getElementById('dynamic_control_rule_list');
            container.innerHTML = '';

            if (dynamicControlRules.length === 0) {
                container.innerHTML = '<div class="text-xs text-gray-400 text-center py-4">No control rules configured. Click Add Control Rule to create one.</div>';
                return;
            }

            for (var i = 0; i < dynamicControlRules.length; i++) {
                var rule = dynamicControlRules[i];
                var card = document.createElement('div');
                card.className = 'bg-black border border-gray-700 rounded p-3 flex justify-between items-center';
                if (!rule.enabled) {
                    card.style.opacity = '0.5';
                }

                var info = document.createElement('div');
                info.className = 'flex-1';
                
                var statusBadge = rule.enabled ? 
                    '<span class="inline-block bg-green-600 text-white text-[10px] px-2 py-0.5 rounded mr-2">ENABLED</span>' : 
                    '<span class="inline-block bg-gray-600 text-white text-[10px] px-2 py-0.5 rounded mr-2">DISABLED</span>';
                
                var rdsParamDisplay = {
                    'ms': 'MS',
                    'pty': 'PTY',
                    'ptyn': 'PTYN',
                    'tp': 'TP',
                    'ta': 'TA',
                    'pi': 'PI'
                }[rule.rds_param] || rule.rds_param;
                
                var mappingInfo = rule.mapping_type;
                if (rule.mapping_type === 'conditional') {
                    var conds = rule.conditions || [];
                    if (!conds.length && rule.condition_value) conds = [{match: rule.condition_value, output: rule.output_value || ''}];
                    if (conds.length === 1) {
                        mappingInfo += ' (If "' + conds[0].match + '" → ' + conds[0].output + ')';
                    } else if (conds.length > 1) {
                        mappingInfo += ' (' + conds.length + ' conditions)';
                    }
                } else if (rule.mapping_type === 'list_match' && rule.match_list && rule.match_list.length > 0) {
                    mappingInfo += ' (' + rule.match_list.length + ' values → ' + (rule.list_output || '?') + ')';
                } else if (rule.custom_mapping && Object.keys(rule.custom_mapping).length > 0) {
                    mappingInfo += ' (' + Object.keys(rule.custom_mapping).length + ' mappings)';
                }
                
                info.innerHTML = statusBadge +
                                '<div class="font-semibold text-sm text-pink-400">' + (rule.name || 'Unnamed Rule') + '</div>' +
                                '<div class="text-xs text-gray-400 mt-1">' + rule.field_path + ' → ' + rdsParamDisplay + '</div>' +
                                '<div class="text-[10px] text-gray-500 mt-1">Poll: ' + (rule.poll_interval || 2) + 's | ' + mappingInfo + '</div>';

                var actions = document.createElement('div');
                actions.className = 'flex gap-2';

                var editBtn = document.createElement('button');
                editBtn.textContent = 'Edit';
                editBtn.className = 'px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-xs';
                editBtn.onclick = (function(idx) { return function() { editDynamicControlRule(idx); }; })(i);

                var delBtn = document.createElement('button');
                delBtn.textContent = 'Delete';
                delBtn.className = 'px-3 py-1 bg-red-600 hover:bg-red-500 rounded text-xs';
                delBtn.onclick = (function(idx) { return function() { deleteDynamicControlRule(idx); }; })(i);

                actions.appendChild(editBtn);
                actions.appendChild(delBtn);
                card.appendChild(info);
                card.appendChild(actions);
                container.appendChild(card);
            }
        }

        var conditionRows = [];

        function renderConditionalRows() {
            var container = document.getElementById('dc_conditions_list');
            if (!container) return;
            var isPTY = document.getElementById('dc_rds_param').value === 'pty';
            container.innerHTML = '';
            if (conditionRows.length === 0) {
                container.innerHTML = '<div class="text-xs text-gray-500 italic py-1">No conditions yet — click + Add Condition.</div>';
                return;
            }
            var ptyNames = ['None','News','Current Affairs','Info','Sport','Education','Drama','Culture','Science','Varied','Pop','Rock','Easy Listening','Light Classical','Serious Classical','Other Music','Weather','Finance',"Children's",'Social','Religion','Phone-In','Travel','Leisure','Jazz','Country','National','Oldies','Folk','Documentary','Alarm Test','Alarm'];
            conditionRows.forEach(function(row, idx) {
                var div = document.createElement('div');
                div.className = 'flex gap-2 items-center';

                var condInput = document.createElement('input');
                condInput.type = 'text';
                condInput.value = row.match || '';
                condInput.placeholder = 'If equals...';
                condInput.className = 'flex-1 min-w-[120px] bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                condInput.oninput = (function(i) { return function() { conditionRows[i].match = this.value; }; })(idx);

                var arrow = document.createElement('span');
                arrow.textContent = '→';
                arrow.className = 'text-gray-400 text-xs flex-shrink-0';

                var outInput;
                if (isPTY) {
                    outInput = document.createElement('select');
                    outInput.className = 'w-28 bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                    for (var p = 0; p <= 31; p++) {
                        var opt = document.createElement('option');
                        opt.value = String(p);
                        opt.textContent = p + ': ' + (ptyNames[p] || p);
                        if (String(p) === String(row.output)) opt.selected = true;
                        outInput.appendChild(opt);
                    }
                    outInput.onchange = (function(i) { return function() { conditionRows[i].output = this.value; }; })(idx);
                } else {
                    outInput = document.createElement('input');
                    outInput.type = 'text';
                    outInput.value = row.output || '';
                    outInput.placeholder = 'Set to...';
                    outInput.className = 'w-32 bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                    outInput.oninput = (function(i) { return function() { conditionRows[i].output = this.value; }; })(idx);
                }

                var delBtn = document.createElement('button');
                delBtn.type = 'button';
                delBtn.textContent = '✕';
                delBtn.className = 'px-2 py-1 bg-red-900 hover:bg-red-700 rounded text-xs flex-shrink-0';
                delBtn.onclick = (function(i) { return function() { conditionRows.splice(i, 1); renderConditionalRows(); }; })(idx);

                div.appendChild(condInput);
                div.appendChild(arrow);
                div.appendChild(outInput);
                div.appendChild(delBtn);
                container.appendChild(div);
            });
        }

        function addDynamicConditionRow() {
            conditionRows.push({ match: '', output: '' });
            renderConditionalRows();
        }

        var multiActionRows = [];

        function renderMultiActionRows() {
            var container = document.getElementById('dc_multi_actions_list');
            if (!container) return;
            container.innerHTML = '';

            multiActionRows.forEach(function(row, idx) {
                var div = document.createElement('div');
                div.className = 'flex gap-2 items-center';

                var paramSelect = document.createElement('select');
                paramSelect.className = 'flex-1 bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                paramSelect.innerHTML = '<option value="">Select param...</option>' +
                    '<option value="pty">PTY</option>' +
                    '<option value="ms">MS</option>' +
                    '<option value="tp">TP</option>' +
                    '<option value="ta">TA</option>' +
                    '<option value="pi">PI</option>' +
                    '<option value="ptyn">PTYN</option>' +
                    '<option value="rt_text">RT Text (static RadioText)</option>' +
                    '<option value="rt_a">RT Buffer A</option>' +
                    '<option value="rt_b">RT Buffer B</option>' +
                    '<option value="pin">PIN (use value=NOW for current time)</option>' +
                    '<option value="en_pin">PIN Enable</option>';
                paramSelect.value = row.param || '';
                paramSelect.onchange = (function(i) { return function() { multiActionRows[i].param = this.value; }; })(idx);

                var arrow = document.createElement('span');
                arrow.textContent = '→';
                arrow.className = 'text-gray-500 flex-shrink-0';

                var valueInput = document.createElement('input');
                valueInput.type = 'text';
                valueInput.value = row.value || '';
                valueInput.placeholder = 'Value (or NOW for PIN)';
                valueInput.className = 'flex-1 bg-black border border-gray-600 rounded px-2 py-1 text-xs';
                valueInput.oninput = (function(i) { return function() { multiActionRows[i].value = this.value; }; })(idx);

                var delBtn = document.createElement('button');
                delBtn.type = 'button';
                delBtn.textContent = '✕';
                delBtn.className = 'px-2 py-1 bg-red-900 hover:bg-red-700 rounded text-xs flex-shrink-0';
                delBtn.onclick = (function(i) { return function() { multiActionRows.splice(i, 1); renderMultiActionRows(); }; })(idx);

                div.appendChild(paramSelect);
                div.appendChild(arrow);
                div.appendChild(valueInput);
                div.appendChild(delBtn);
                container.appendChild(div);
            });
        }

        function addMultiActionRow() {
            multiActionRows.push({ param: '', value: '' });
            renderMultiActionRows();
        }

        function addDynamicControlRule() {
            document.getElementById('dc_edit_idx').value = '';
            document.getElementById('dc_name').value = '';
            document.getElementById('dc_url').value = '';
            document.getElementById('dc_field_path').value = '';
            document.getElementById('dc_rds_param').value = '';
            document.getElementById('dc_mapping_type').value = 'direct';
            document.getElementById('dc_poll_interval').value = '5';
            document.getElementById('dc_enabled').checked = true;
            document.getElementById('dc_value_mappings').innerHTML = '';
            conditionRows = [];
            multiActionRows = [];
            var dcMatchList = document.getElementById('dc_match_list'); if (dcMatchList) dcMatchList.value = '';
            var dcListOutput = document.getElementById('dc_list_output'); if (dcListOutput) dcListOutput.value = '';
            var dcKeywords = document.getElementById('dc_keywords'); if (dcKeywords) dcKeywords.value = '';
            ptyMappings = {};
            currentDynamicControlFieldValue = null;
            currentDynamicControlFieldType = null;
            document.getElementById('dc_quick_setup').style.display = 'none';
            closeDynamicControlBrowser();
            updateDynamicControlParamUI();
            document.getElementById('dynamic_control_modal_title').textContent = 'Add Control Rule';
            document.getElementById('dynamic_control_edit_form').style.display = 'block';
        }

        function editDynamicControlRule(idx) {
            var rule = dynamicControlRules[idx];
            document.getElementById('dc_edit_idx').value = idx;
            document.getElementById('dc_name').value = rule.name || '';
            document.getElementById('dc_url').value = rule.url || '';
            document.getElementById('dc_field_path').value = rule.field_path || '';
            document.getElementById('dc_rds_param').value = rule.rds_param || '';
            document.getElementById('dc_mapping_type').value = rule.mapping_type || 'direct';
            document.getElementById('dc_poll_interval').value = rule.poll_interval || 5;
            document.getElementById('dc_enabled').checked = rule.enabled !== false;
            
            // Clear existing mappings
            document.getElementById('dc_value_mappings').innerHTML = '';
            conditionRows = [];
            multiActionRows = [];
            var lmEl = document.getElementById('dc_match_list'); if (lmEl) lmEl.value = '';
            var loEl = document.getElementById('dc_list_output'); if (loEl) loEl.value = '';
            var kwEl = document.getElementById('dc_keywords'); if (kwEl) kwEl.value = '';
            ptyMappings = {};
            currentDynamicControlFieldValue = null;
            currentDynamicControlFieldType = null;
            document.getElementById('dc_quick_setup').style.display = 'none';
            
            // Load PTY mappings if present
            if (rule.custom_mapping && rule.rds_param === 'pty') {
                ptyMappings = rule.custom_mapping;
            }
            
            // Load custom value mappings
            if (rule.custom_mapping && (rule.mapping_type === 'text_match' || rule.mapping_type === 'boolean')) {
                var container = document.getElementById('dc_value_mappings');
                Object.keys(rule.custom_mapping).forEach(function(key) {
                    addDynamicControlValueMapping();
                    var rows = container.querySelectorAll('.flex');
                    var lastRow = rows[rows.length - 1];
                    lastRow.children[0].value = key;
                    lastRow.children[1].value = rule.custom_mapping[key];
                });
            }

            // Load conditional rows
            if (rule.mapping_type === 'conditional') {
                if (rule.conditions && rule.conditions.length > 0) {
                    conditionRows = rule.conditions.map(function(c) { return { match: c.match || '', output: String(c.output || '') }; });
                } else if (rule.condition_value) {
                    // Backward compat: single condition
                    conditionRows = [{ match: rule.condition_value, output: String(rule.output_value || '') }];
                }
            }

            // Load list_match fields
            if (rule.mapping_type === 'list_match') {
                var matchListEl = document.getElementById('dc_match_list');
                var listOutputEl = document.getElementById('dc_list_output');
                if (matchListEl) matchListEl.value = (rule.match_list || []).join('\n');
                if (listOutputEl) listOutputEl.value = rule.list_output || '';
            }

            updateDynamicControlParamUI();
            
            document.getElementById('dynamic_control_modal_title').textContent = 'Edit Control Rule';
            document.getElementById('dynamic_control_edit_form').style.display = 'block';
        }

        function deleteDynamicControlRule(idx) {
            if (!confirm('Delete this control rule?')) return;
            dynamicControlRules.splice(idx, 1);
            syncDynamicControlRules();
            renderDynamicControlRuleList();
            updateDynamicControlDisplay();
        }

        function cancelDynamicControlEdit() {
            document.getElementById('dynamic_control_edit_form').style.display = 'none';
            document.getElementById('dynamic_control_modal_title').textContent = 'Basic Dynamic Control';
        }

        function saveDynamicControlRule() {
            var idx = document.getElementById('dc_edit_idx').value;
            var rdsParam = document.getElementById('dc_rds_param').value;
            
            // Build custom mapping based on parameter type
            var customMapping = null;
            var mappingType = document.getElementById('dc_mapping_type').value;
            
            if (rdsParam === 'pty' && mappingType === 'pty_name' && Object.keys(ptyMappings).length > 0) {
                // PTY name mappings (only when explicitly using pty_name mapping type)
                customMapping = ptyMappings;
            } else if (mappingType === 'text_match') {
                // Custom value mappings
                customMapping = {};
                var rows = document.getElementById('dc_value_mappings').querySelectorAll('.flex');
                rows.forEach(function(row) {
                    var jsonVal = row.children[0].value.trim();
                    var rdsVal = row.children[1].value.trim();
                    if (jsonVal && rdsVal) {
                        customMapping[jsonVal] = rdsVal;
                    }
                });
                if (Object.keys(customMapping).length === 0) {
                    customMapping = null;
                }
            } else if (mappingType === 'conditional') {
                // Conditional mapping - no custom_mapping needed, use separate fields
                customMapping = null;
            } else if (mappingType === 'list_match') {
                // List match - no custom_mapping needed, use separate fields
                customMapping = null;
            }
            
            var rule = {
                name: document.getElementById('dc_name').value || 'Unnamed Rule',
                url: document.getElementById('dc_url').value || '',
                field_path: document.getElementById('dc_field_path').value || '',
                rds_param: rdsParam,
                mapping_type: mappingType,
                custom_mapping: customMapping,
                poll_interval: parseInt(document.getElementById('dc_poll_interval').value) || 5,
                enabled: document.getElementById('dc_enabled').checked
            };
            
            // Add conditional mapping fields if applicable
            if (mappingType === 'conditional') {
                var validConds = conditionRows.filter(function(r) { return r.match && r.match.trim(); });
                if (!validConds.length) {
                    alert('At least one condition is required for conditional mapping');
                    return;
                }
                rule.conditions = validConds.map(function(r) { return { match: r.match.trim(), output: String(r.output || '') }; });
                // Backward compat single fields (first condition)
                rule.condition_value = rule.conditions[0].match;
                rule.output_value = rule.conditions[0].output;
            }

            // Add list_match fields if applicable
            if (mappingType === 'list_match') {
                var matchListEl = document.getElementById('dc_match_list');
                var listOutputEl = document.getElementById('dc_list_output');
                var listText = matchListEl ? matchListEl.value.trim() : '';
                rule.match_list = listText ? listText.split('\n').map(function(s) { return s.trim(); }).filter(function(s) { return s.length > 0; }) : [];
                rule.list_output = listOutputEl ? listOutputEl.value.trim() : '';

                if (!rule.match_list.length) {
                    alert('At least one match value is required for list match');
                    return;
                }
                if (!rule.list_output) {
                    alert('Output value is required for list match');
                    return;
                }
            }

            // Validation
            if (!rule.url) {
                alert('JSON URL is required');
                return;
            }
            if (!rule.field_path) {
                alert('Please select a JSON field (click "Test & Browse")');
                return;
            }
            if (!rule.rds_param) {
                alert('RDS Parameter is required');
                return;
            }

            if (idx === '') {
                dynamicControlRules.push(rule);
            } else {
                dynamicControlRules[parseInt(idx)] = rule;
            }

            syncDynamicControlRules();
            renderDynamicControlRuleList();
            updateDynamicControlDisplay();
            cancelDynamicControlEdit();
        }

        function syncDynamicControlRules() {
            var hiddenInput = document.getElementById('dynamic_control_rules');
            if (hiddenInput) {
                hiddenInput.value = JSON.stringify(dynamicControlRules);
            }
            socket.emit('update', { dynamic_control_rules: JSON.stringify(dynamicControlRules) });
        }

        function updateDynamicControlDisplay() {
            var enabledCount = dynamicControlRules.filter(function(r) { return r.enabled !== false; }).length;
            var totalCount = dynamicControlRules.length;
            var displayElem = document.getElementById('dynamic_control_rules_display');
            if (displayElem) {
                displayElem.textContent = enabledCount + ' active / ' + totalCount + ' total rules';
            }
        }

        var currentDynamicControlJSON = null;
        var currentDynamicControlFieldValue = null;
        var currentDynamicControlFieldType = null;

        function testDynamicControlURL() {
            var url = document.getElementById('dc_url').value.trim();
            if (!url) {
                alert('Please enter a JSON URL first');
                return;
            }

            var browser = document.getElementById('dc_json_browser');
            var tree = document.getElementById('dc_json_tree');
            tree.innerHTML = '<div class="text-gray-400">🔄 Fetching JSON...</div>';
            browser.style.display = 'block';

            // Use backend proxy to avoid CORS issues
            fetch('/dynamic_control/fetch_json', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url: url})
            })
                .then(function(response) {
                    return response.json().then(function(data) {
                        if (!response.ok) {
                            throw new Error(data.error || 'HTTP ' + response.status);
                        }
                        return data;
                    });
                })
                .then(function(result) {
                    if (result.success && result.data) {
                        currentDynamicControlJSON = result.data;
                        renderDynamicControlJSONTree(result.data, tree, '');
                    } else {
                        throw new Error(result.error || 'Unknown error');
                    }
                })
                .catch(function(error) {
                    tree.innerHTML = '<div class="text-red-400">✗ ' + error.message + '</div>' +
                                    '<div class="text-[10px] text-gray-500 mt-2">Tips: Check URL is correct and accessible from this server</div>';
                });
        }

        function renderDynamicControlJSONTree(obj, container, path) {
            container.innerHTML = '';
            
            function renderNode(value, key, currentPath) {
                var fullPath = currentPath ? currentPath + '.' + key : key;
                var div = document.createElement('div');
                div.className = 'py-0.5';
                
                if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
                    // Object node (expandable)
                    var header = document.createElement('div');
                    header.className = 'text-gray-400 cursor-pointer hover:text-white';
                    header.innerHTML = '▶ ' + key + ' <span class="text-gray-600">{...}</span>';
                    
                    var children = document.createElement('div');
                    children.className = 'pl-4 border-l border-gray-700 ml-2';
                    children.style.display = 'none';
                    
                    header.onclick = function(e) {
                        e.stopPropagation();
                        if (children.style.display === 'none') {
                            children.style.display = 'block';
                            header.innerHTML = '▼ ' + key + ' <span class="text-gray-600">{...}</span>';
                            // Render children on first expand
                            if (!children.hasChildNodes()) {
                                Object.keys(value).forEach(function(childKey) {
                                    children.appendChild(renderNode(value[childKey], childKey, fullPath));
                                });
                            }
                        } else {
                            children.style.display = 'none';
                            header.innerHTML = '▶ ' + key + ' <span class="text-gray-600">{...}</span>';
                        }
                    };
                    
                    div.appendChild(header);
                    div.appendChild(children);
                } else if (Array.isArray(value)) {
                    // Array node
                    var header = document.createElement('div');
                    header.className = 'text-gray-400';
                    header.innerHTML = '▶ ' + key + ' <span class="text-gray-600">[' + value.length + ' items]</span>';
                    div.appendChild(header);
                } else {
                    // Leaf node (clickable)
                    var leaf = document.createElement('div');
                    leaf.className = 'cursor-pointer hover:bg-gray-800 px-2 py-1 rounded transition-colors';
                    
                    var valueStr = JSON.stringify(value);
                    if (valueStr.length > 40) valueStr = valueStr.substring(0, 40) + '...';
                    
                    var typeColor = typeof value === 'number' ? 'text-blue-400' : 
                                   typeof value === 'boolean' ? 'text-yellow-400' : 
                                   value === null ? 'text-gray-500' :
                                   'text-green-400';
                    
                    leaf.innerHTML = '<span class="text-cyan-400">' + key + '</span>: ' +
                                    '<span class="' + typeColor + '">' + valueStr + '</span> ' +
                                    '<span class="text-[10px] text-gray-500">(' + typeof value + ')</span>';
                    
                    leaf.onclick = function(e) {
                        e.stopPropagation();
                        document.getElementById('dc_field_path').value = fullPath;
                        // Store value and type for smart suggestions
                        currentDynamicControlFieldValue = value;
                        currentDynamicControlFieldType = typeof value;
                        // Visual feedback
                        leaf.style.backgroundColor = '#065f46';
                        setTimeout(function() { leaf.style.backgroundColor = ''; }, 300);
                        // Show quick setup suggestions
                        showDynamicControlQuickSetup();
                    };
                    
                    div.appendChild(leaf);
                }
                
                return div;
            }
            
            Object.keys(obj).forEach(function(key) {
                container.appendChild(renderNode(obj[key], key, path));
            });
        }

        function suggestDynamicControlMapping(value) {
            var mappingType = document.getElementById('dc_mapping_type');
            if (typeof value === 'boolean') {
                mappingType.value = 'boolean';
            } else if (typeof value === 'number' && (value === 0 || value === 1)) {
                mappingType.value = 'direct';
            } else if (typeof value === 'string') {
                mappingType.value = 'passthrough';
            }
        }

        function closeDynamicControlBrowser() {
            document.getElementById('dc_json_browser').style.display = 'none';
        }

        function showDynamicControlQuickSetup() {
            var quickSetup = document.getElementById('dc_quick_setup');
            var suggestions = document.getElementById('dc_quick_suggestions');
            var param = document.getElementById('dc_rds_param').value;
            
            suggestions.innerHTML = '';
            
            if (!param || currentDynamicControlFieldValue === null) {
                quickSetup.style.display = 'none';
                return;
            }
            
            // Generate suggestions based on parameter and value type
            if (param === 'ms' || param === 'tp' || param === 'ta') {
                // Binary parameters - suggest both options
                quickSetup.style.display = 'block';
                
                if (currentDynamicControlFieldType === 'boolean') {
                    suggestions.innerHTML = 
                        '<div class="text-xs text-blue-200 mb-2">Detected boolean field. Click to set up:</div>' +
                        '<div class="flex gap-2">' +
                        '<button onclick="applyQuickMapping(\'boolean\', {\'true\': 1, \'false\': 0})" class="flex-1 bg-green-700 hover:bg-green-600 text-white rounded px-3 py-2 text-xs">' +
                        '<div class="font-bold">Use Boolean</div>' +
                        '<div class="text-[10px] opacity-80">true → 1, false → 0</div>' +
                        '</button>' +
                        '</div>';
                } else if (currentDynamicControlFieldType === 'number') {
                    suggestions.innerHTML = 
                        '<div class="text-xs text-blue-200 mb-2">Detected numeric field. Click to set up:</div>' +
                        '<div class="flex gap-2">' +
                        '<button onclick="applyQuickMapping(\'direct\', null)" class="flex-1 bg-green-700 hover:bg-green-600 text-white rounded px-3 py-2 text-xs">' +
                        '<div class="font-bold">Direct Mapping</div>' +
                        '<div class="text-[10px] opacity-80">0 → 0, 1 → 1</div>' +
                        '</button>' +
                        '</div>';
                } else if (currentDynamicControlFieldType === 'string') {
                    suggestions.innerHTML = 
                        '<div class="text-xs text-blue-200 mb-2">Detected text field. Set up custom mappings:</div>' +
                        '<div class="flex gap-2">' +
                        '<button onclick="applyQuickMapping(\'text_match\', {\'Music\': 0, \'Speech\': 1})" class="flex-1 bg-green-700 hover:bg-green-600 text-white rounded px-3 py-2 text-xs">' +
                        '<div class="font-bold">Music/Speech</div>' +
                        '<div class="text-[10px] opacity-80">"Music" → 0, "Speech" → 1</div>' +
                        '</button>' +
                        '<button onclick="showCustomMappingBuilder()" class="flex-1 bg-blue-700 hover:bg-blue-600 text-white rounded px-3 py-2 text-xs">' +
                        '<div class="font-bold">Custom</div>' +
                        '<div class="text-[10px] opacity-80">Define your own</div>' +
                        '</button>' +
                        '</div>';
                }
            } else if (param === 'ptyn' || param === 'pi') {
                quickSetup.style.display = 'block';
                suggestions.innerHTML = 
                    '<div class="text-xs text-blue-200 mb-2">Pass-through mode - field value will be used directly</div>' +
                    '<button onclick="applyQuickMapping(\'passthrough\', null)" class="w-full bg-green-700 hover:bg-green-600 text-white rounded px-3 py-2 text-xs">' +
                    '<div class="font-bold">✓ Use Pass-Through</div>' +
                    '<div class="text-[10px] opacity-80">JSON value → RDS parameter</div>' +
                    '</button>';
            } else {
                quickSetup.style.display = 'none';
            }
        }

        function applyQuickMapping(mappingType, customMapping) {
            document.getElementById('dc_mapping_type').value = mappingType;
            
            if (customMapping) {
                // Pre-populate value mappings
                var container = document.getElementById('dc_value_mappings');
                container.innerHTML = '';
                Object.keys(customMapping).forEach(function(key) {
                    addDynamicControlValueMapping();
                    var rows = container.querySelectorAll('.flex');
                    var lastRow = rows[rows.length - 1];
                    lastRow.children[0].value = key;
                    lastRow.children[1].value = customMapping[key];
                });
                document.getElementById('dc_custom_mapping_section').style.display = 'block';
            }
            
            updateDynamicControlParamUI();
            document.getElementById('dc_quick_setup').style.display = 'none';
        }

        function showCustomMappingBuilder() {
            document.getElementById('dc_mapping_type').value = 'text_match';
            updateDynamicControlParamUI();
            document.getElementById('dc_quick_setup').style.display = 'none';
        }

        function updateDynamicControlParamUI() {
            var param = document.getElementById('dc_rds_param').value;
            var autoMapping = document.getElementById('dc_mapping_auto');
            var ptyMapping = document.getElementById('dc_pty_mapping');
            var customMapping = document.getElementById('dc_custom_mapping_section');
            var conditionalMapping = document.getElementById('dc_conditional_section');
            var listMatchSection = document.getElementById('dc_list_match_section');
            var mappingTypeElement = document.getElementById('dc_mapping_type');

            if (!mappingTypeElement) {
                console.error('[DEBUG] dc_mapping_type element not found!');
                return;
            }

            var mappingType = mappingTypeElement.value;

            // Hide all
            autoMapping.style.display = 'none';
            ptyMapping.style.display = 'none';
            customMapping.style.display = 'none';
            conditionalMapping.style.display = 'none';
            if (listMatchSection) listMatchSection.style.display = 'none';
            
            // Reset conditional inputs visibility
            document.getElementById('dc_output_value').style.display = 'block';
            document.getElementById('dc_output_pty').style.display = 'none';
            
            if (param === 'pty') {
                // Show both mapping type options AND PTY mapping interface
                autoMapping.style.display = 'block';
                
                if (mappingType === 'pty_name') {
                    // Show PTY mapping interface for pty_name mapping
                    ptyMapping.style.display = 'block';
                    renderPTYMappingList();
                } else if (mappingType === 'text_match' || document.getElementById('dc_value_mappings').children.length > 0) {
                    customMapping.style.display = 'block';
                    updateCurrentMappingsDisplay();
                } else if (mappingType === 'conditional') {
                    conditionalMapping.style.display = 'block';
                    // Show PTY dropdown for conditional PTY mapping
                    document.getElementById('dc_output_value').style.display = 'none';
                    document.getElementById('dc_output_pty').style.display = 'block';
                } else if (mappingType === 'list_match') {
                    if (listMatchSection) listMatchSection.style.display = 'block';
                }
            } else if (param === 'ms' || param === 'tp' || param === 'ta') {
                // Show simple mapping options
                autoMapping.style.display = 'block';
                if (mappingType === 'text_match' || document.getElementById('dc_value_mappings').children.length > 0) {
                    customMapping.style.display = 'block';
                    updateCurrentMappingsDisplay();
                } else if (mappingType === 'conditional') {
                    conditionalMapping.style.display = 'block';
                    // Show text input for non-PTY conditional mapping
                    document.getElementById('dc_output_value').style.display = 'block';
                    document.getElementById('dc_output_pty').style.display = 'none';
                } else if (mappingType === 'list_match') {
                    if (listMatchSection) listMatchSection.style.display = 'block';
                }
                // Show quick setup if field is selected
                if (currentDynamicControlFieldValue !== null) {
                    showDynamicControlQuickSetup();
                }
            } else if (param === 'ptyn' || param === 'pi' || param === 'rt_text' || param === 'rt_a' || param === 'rt_b') {
                // Allow all mapping types for PTYN, PI, and RT text parameters
                autoMapping.style.display = 'block';
                if (mappingType === 'text_match' || document.getElementById('dc_value_mappings').children.length > 0) {
                    customMapping.style.display = 'block';
                    updateCurrentMappingsDisplay();
                } else if (mappingType === 'conditional') {
                    conditionalMapping.style.display = 'block';
                    // Show text input for non-PTY conditional mapping
                    document.getElementById('dc_output_value').style.display = 'block';
                    document.getElementById('dc_output_pty').style.display = 'none';
                } else if (mappingType === 'list_match') {
                    if (listMatchSection) listMatchSection.style.display = 'block';
                }
                // Show quick setup
                if (currentDynamicControlFieldValue !== null) {
                    showDynamicControlQuickSetup();
                }
            } else if (param) {
                autoMapping.style.display = 'block';
                if (mappingType === 'text_match' || document.getElementById('dc_value_mappings').children.length > 0) {
                    customMapping.style.display = 'block';
                    updateCurrentMappingsDisplay();
                } else if (mappingType === 'conditional') {
                    conditionalMapping.style.display = 'block';
                    // Show text input for non-PTY conditional mapping
                    document.getElementById('dc_output_value').style.display = 'block';
                    document.getElementById('dc_output_pty').style.display = 'none';
                } else if (mappingType === 'list_match') {
                    if (listMatchSection) listMatchSection.style.display = 'block';
                }
            }

            // Re-render condition rows whenever param/mapping type changes
            if (mappingType === 'conditional') renderConditionalRows();
        }

        function updateCurrentMappingsDisplay() {
            var container = document.getElementById('dc_current_mappings');
            var valueMappings = document.getElementById('dc_value_mappings');
            var rows = valueMappings.querySelectorAll('.flex');
            
            if (rows.length > 0) {
                var mappings = [];
                rows.forEach(function(row) {
                    var jsonVal = row.children[0].value.trim();
                    var rdsVal = row.children[1].value.trim();
                    if (jsonVal && rdsVal) {
                        mappings.push('<span class="text-cyan-400">"' + jsonVal + '"</span> → <span class="text-pink-400">' + rdsVal + '</span>');
                    }
                });
                
                if (mappings.length > 0) {
                    container.style.display = 'block';
                    container.className = 'mb-2 bg-green-900 border border-green-700 rounded p-2 text-xs';
                    container.innerHTML = '<div class="font-bold mb-1 text-green-200">✓ Current Mappings:</div>' + mappings.join('<br>');
                } else {
                    container.style.display = 'none';
                }
            } else {
                container.style.display = 'none';
            }
        }

        function renderPTYMappingList() {
            var ptyList = document.getElementById('dc_pty_list');
            var ptyTypes = [
                {code: 0, name: "None"}, {code: 1, name: "News"}, {code: 2, name: "Current Affairs"},
                {code: 3, name: "Information"}, {code: 4, name: "Sport"}, {code: 5, name: "Education"},
                {code: 6, name: "Drama"}, {code: 7, name: "Culture"}, {code: 8, name: "Science"},
                {code: 9, name: "Varied"}, {code: 10, name: "Pop Music"}, {code: 11, name: "Rock Music"},
                {code: 12, name: "Easy Listening"}, {code: 13, name: "Light Classical"}, 
                {code: 14, name: "Serious Classical"}, {code: 15, name: "Other Music"},
                {code: 16, name: "Weather"}, {code: 17, name: "Finance"}, {code: 18, name: "Children's"},
                {code: 19, name: "Social Affairs"}, {code: 20, name: "Religion"}, {code: 21, name: "Phone-In"},
                {code: 22, name: "Travel"}, {code: 23, name: "Leisure"}, {code: 24, name: "Jazz"},
                {code: 25, name: "Country"}, {code: 26, name: "National Music"}, {code: 27, name: "Oldies"},
                {code: 28, name: "Folk Music"}, {code: 29, name: "Documentary"}, {code: 30, name: "Alarm Test"},
                {code: 31, name: "Alarm"}
            ];
            
            ptyList.innerHTML = '';
            
            // Show current mappings if any
            if (Object.keys(ptyMappings).length > 0) {
                var summaryDiv = document.createElement('div');
                summaryDiv.className = 'bg-green-900 border border-green-700 rounded p-2 mb-3 text-xs';
                var mappingText = Object.keys(ptyMappings).map(function(key) {
                    var ptyName = ptyTypes.find(function(p) { return p.code === ptyMappings[key]; });
                    return '"<span class="text-cyan-400">' + key + '</span>" → <span class="text-pink-400">' + 
                           (ptyName ? ptyName.name : ptyMappings[key]) + '</span>';
                }).join('<br>');
                summaryDiv.innerHTML = '<div class="font-bold mb-1">✓ Current Mappings:</div>' + mappingText;
                ptyList.appendChild(summaryDiv);
            }
            
            var helperDiv = document.createElement('div');
            helperDiv.className = 'text-[10px] text-gray-400 mb-2 p-2 bg-blue-900 border border-blue-700 rounded';
            helperDiv.textContent = 'Click a PTY below to map JSON values to it. Example: map "sport" or "Sport" to PTY 4 (Sport)';
            ptyList.appendChild(helperDiv);
            
            ptyTypes.forEach(function(pty) {
                var item = document.createElement('div');
                var isMapped = Object.values(ptyMappings).indexOf(pty.code) >= 0;
                item.className = 'flex items-center justify-between bg-black hover:bg-gray-800 px-2 py-1 rounded cursor-pointer text-xs border border-gray-700' +
                                (isMapped ? ' bg-green-900 border-green-700' : '');
                item.innerHTML = '<span><span class="text-pink-400 font-mono w-6 inline-block">' + pty.code + '</span> ' + pty.name + '</span>' +
                                '<button class="text-green-500 hover:text-green-400 text-xs">+ Map</button>';
                item.onclick = function() {
                    promptPTYValueMapping(pty.code, pty.name);
                };
                ptyList.appendChild(item);
            });
        }

        var ptyMappings = {};

        function promptPTYValueMapping(code, name) {
            var jsonValue = prompt('What JSON value should map to PTY ' + code + ' (' + name + ')?\n\nExample: "sport", "Sport", "4"');
            if (jsonValue) {
                ptyMappings[jsonValue] = code;
                renderPTYMappingList();
            }
        }

        function updateMappingSummary() {
            // Deprecated - now handled in renderPTYMappingList()
        }

        function addDynamicControlValueMapping() {
            var container = document.getElementById('dc_value_mappings');
            var row = document.createElement('div');
            row.className = 'flex gap-2';
            
            var jsonInput = document.createElement('input');
            jsonInput.type = 'text';
            jsonInput.placeholder = 'JSON value';
            jsonInput.className = 'flex-1 bg-black border border-gray-600 rounded px-2 py-1 text-xs';
            jsonInput.onchange = updateCurrentMappingsDisplay;
            jsonInput.onkeyup = updateCurrentMappingsDisplay;
            
            var rdsInput = document.createElement('input');
            rdsInput.type = 'text';
            rdsInput.placeholder = '→ RDS value';
            rdsInput.className = 'flex-1 bg-black border border-gray-600 rounded px-2 py-1 text-xs';
            rdsInput.onchange = updateCurrentMappingsDisplay;
            rdsInput.onkeyup = updateCurrentMappingsDisplay;
            
            var removeBtn = document.createElement('button');
            removeBtn.textContent = '✕';
            removeBtn.className = 'text-red-400 hover:text-red-300 text-xs px-2';
            removeBtn.onclick = function() {
                row.remove();
                updateCurrentMappingsDisplay();
            };
            
            row.appendChild(jsonInput);
            row.appendChild(rdsInput);
            row.appendChild(removeBtn);
            container.appendChild(row);
            
            // Focus the first input
            jsonInput.focus();
            updateCurrentMappingsDisplay();
        }


        // Custom ODA Management
        var customODAList = [];

        function groupTypeToString(groupType) {
            // Convert numeric group type (0-31) to string format (0A-15B)
            var gt = parseInt(groupType) || 0;
            var type = Math.floor(gt / 2);
            var version = (gt % 2 === 0) ? 'A' : 'B';
            return type + version;
        }

        function loadCustomODAList() {
            try {
                var hiddenInput = document.getElementById('custom_oda_list');
                if (hiddenInput && hiddenInput.value) {
                    customODAList = JSON.parse(hiddenInput.value);
                } else {
                    customODAList = [];
                }
            } catch (e) {
                customODAList = [];
            }
            updateCustomODADisplay();
        }

        function openCustomODAModal() {
            document.getElementById('custom_oda_modal').style.display = 'flex';
            renderCustomODAList();
        }

        function closeCustomODAModal() {
            document.getElementById('custom_oda_modal').style.display = 'none';
            document.getElementById('custom_oda_edit_form').style.display = 'none';
        }

        function renderCustomODAList() {
            var list = document.getElementById('custom_oda_list_modal');
            if (!list) return;

            if (customODAList.length === 0) {
                list.innerHTML = '<div class="text-xs text-gray-500">No custom ODAs configured</div>';
                return;
            }

            var html = '';
            for (var i = 0; i < customODAList.length; i++) {
                var oda = customODAList[i];
                var enabled = oda.enabled !== false;
                var statusColor = enabled ? 'text-green-400' : 'text-gray-500';
                var statusText = enabled ? '✓ Enabled' : '✗ Disabled';

                html += '<div class="p-3 bg-gray-800 hover:bg-gray-700 rounded cursor-pointer border border-gray-700 hover:border-indigo-600 transition-colors" onclick="editCustomODA(' + i + ')">';
                html += '<div class="flex justify-between items-start mb-1">';
                html += '<div class="font-semibold text-white">' + (oda.name || 'Unnamed ODA') + '</div>';
                html += '<div class="text-[10px] ' + statusColor + '">' + statusText + '</div>';
                html += '</div>';
                html += '<div class="flex gap-4 text-[11px]">';
                html += '<span class="text-gray-400">AID:</span><span class="font-mono text-pink-400">' + (oda.aid || '0000').toUpperCase() + '</span>';
                html += '<span class="text-gray-400">Group:</span><span class="text-blue-400">' + groupTypeToString(oda.group_type || 0) + '</span>';
                html += '</div>';
                html += '<div class="flex justify-end gap-2 mt-2">';
                html += '<button onclick="event.stopPropagation(); deleteCustomODA(' + i + ')" class="text-[10px] px-2 py-1 bg-red-700 hover:bg-red-600 rounded">Delete</button>';
                html += '</div>';
                html += '</div>';
            }
            list.innerHTML = html;
        }

        function addCustomODA() {
            document.getElementById('oda_edit_idx').value = '';
            document.getElementById('oda_name').value = '';
            document.getElementById('oda_aid').value = '';
            document.getElementById('oda_group_type').value = '22'; // Default to 11A
            document.getElementById('oda_msg').value = '0000';
            document.getElementById('oda_enabled').checked = true;
            document.getElementById('custom_oda_edit_form').style.display = 'block';
        }

        function editCustomODA(idx) {
            var oda = customODAList[idx];
            document.getElementById('oda_edit_idx').value = idx;
            document.getElementById('oda_name').value = oda.name || '';
            document.getElementById('oda_aid').value = (oda.aid || '').toUpperCase();
            document.getElementById('oda_group_type').value = oda.group_type || 0;
            document.getElementById('oda_msg').value = (oda.msg || '0000').toUpperCase();
            document.getElementById('oda_enabled').checked = oda.enabled !== false;
            document.getElementById('custom_oda_edit_form').style.display = 'block';
        }

        function cancelCustomODAEdit() {
            document.getElementById('custom_oda_edit_form').style.display = 'none';
        }

        async function saveCustomODA() {
            var idx = document.getElementById('oda_edit_idx').value;
            var name = document.getElementById('oda_name').value.trim();
            var aid = document.getElementById('oda_aid').value.trim().toUpperCase();
            var groupType = parseInt(document.getElementById('oda_group_type').value) || 0;
            var msg = document.getElementById('oda_msg').value.trim().toUpperCase() || '0000';
            var enabled = document.getElementById('oda_enabled').checked;

            if (!name || !aid) {
                alert('Please fill in Application Name and AID');
                return;
            }

            if (!/^[0-9A-F]{4}$/.test(aid)) {
                alert('AID must be a 4-digit hex value (e.g., 4BD7)');
                return;
            }

            var oda = {
                name: name,
                aid: aid,
                group_type: groupType,
                msg: msg,
                enabled: enabled
            };

            if (idx === '') {
                customODAList.push(oda);
            } else {
                customODAList[parseInt(idx)] = oda;
            }

            await syncCustomODAList();
            renderCustomODAList();
            cancelCustomODAEdit();
        }

        async function deleteCustomODA(idx) {
            if (!confirm('Delete this ODA?')) return;
            customODAList.splice(idx, 1);
            await syncCustomODAList();
            renderCustomODAList();
        }

        async function syncCustomODAList() {
            var hiddenInput = document.getElementById('custom_oda_list');
            if (hiddenInput) {
                hiddenInput.value = JSON.stringify(customODAList);
            }
            updateCustomODADisplay();
            await sync();
        }

        function updateCustomODADisplay() {
            var display = document.getElementById('custom_oda_display');
            if (!display) return;

            if (customODAList.length === 0) {
                display.innerHTML = '<div class="text-xs text-gray-400">No custom ODAs configured</div>';
                return;
            }

            var html = '<div class="text-xs space-y-1">';
            var enabledCount = 0;
            for (var i = 0; i < customODAList.length; i++) {
                var oda = customODAList[i];
                var enabled = oda.enabled !== false;
                if (enabled) enabledCount++;
                var statusIcon = enabled ? '<span class="text-green-400">✓</span>' : '<span class="text-gray-600">✗</span>';
                html += '<div class="p-2 bg-gray-800 rounded flex justify-between items-center">';
                html += '<div>';
                html += '<div class="font-mono text-pink-400">' + (oda.aid || '0000').toUpperCase() + '</div>';
                html += '<div class="text-gray-300">' + (oda.name || 'Unnamed') + '</div>';
                html += '</div>';
                html += '<div>' + statusIcon + '</div>';
                html += '</div>';
            }
            html += '</div>';
            html += '<div class="text-[10px] text-gray-500 mt-2">' + enabledCount + ' enabled, ' + (customODAList.length - enabledCount) + ' disabled</div>';
            display.innerHTML = html;
        }

        // Custom Groups Management
        var customGroups = [];

        function loadCustomGroups() {
            try {
                var hiddenInput = document.getElementById('custom_groups');
                if (hiddenInput && hiddenInput.value) {
                    customGroups = JSON.parse(hiddenInput.value);
                }
            } catch (e) {
                customGroups = [];
            }
        }

        function openCustomGroupsModal() {
            loadCustomGroups();
            renderCustomGroupsList();
            document.getElementById('custom_groups_modal').style.display = 'flex';
            document.getElementById('custom_group_edit_form').style.display = 'none';
        }

        function closeCustomGroupsModal() {
            document.getElementById('custom_groups_modal').style.display = 'none';
            document.getElementById('custom_group_edit_form').style.display = 'none';
        }

        var modalExpandedGroups = {}; // Track expansion state for modal

        function renderCustomGroupsList() {
            var container = document.getElementById('custom_groups_list');
            container.innerHTML = '';

            if (customGroups.length === 0) {
                container.innerHTML = '<div class="text-xs text-gray-400 text-center py-4">No custom groups configured. Click Add Custom Group to create one.</div>';
                return;
            }

            // Group by type+version
            var grouped = {};
            for (var i = 0; i < customGroups.length; i++) {
                var grp = customGroups[i];
                var verLabel = grp.version == 1 ? 'B' : 'A';
                var key = grp.type + verLabel;
                if (!grouped[key]) {
                    grouped[key] = [];
                }
                grouped[key].push({index: i, group: grp});
            }

            // Sort group keys
            var keys = Object.keys(grouped).sort(function(a, b) {
                var aNum = parseInt(a);
                var bNum = parseInt(b);
                if (aNum !== bNum) return aNum - bNum;
                return a.localeCompare(b);
            });

            // Render each group type
            for (var k = 0; k < keys.length; k++) {
                var key = keys[k];
                var items = grouped[key];
                var isExpanded = modalExpandedGroups[key];
                var expandIcon = isExpanded ? '▼' : '▶';

                // Group header (clickable to expand/collapse)
                var groupHeader = document.createElement('div');
                groupHeader.className = 'bg-gradient-to-r from-purple-900 to-purple-800 border border-purple-600 rounded-t px-3 py-2 mt-3 first:mt-0 cursor-pointer hover:from-purple-800 hover:to-purple-700 transition-colors';
                var allEnabled = items.every(function(item) { return item.group.enabled !== false; });
                var toggleLabel = allEnabled ? 'Disable All' : 'Enable All';
                var toggleBtnClass = allEnabled ? 'bg-red-700 hover:bg-red-600' : 'bg-green-700 hover:bg-green-600';
                var toggleTarget = !allEnabled;
                groupHeader.innerHTML = '<div class="font-mono text-sm font-bold text-purple-200 flex items-center justify-between"><span><span class="inline-block w-4">' + expandIcon + '</span> Group ' + key + ' <span class="text-xs text-purple-300">(' + items.length + ' item' + (items.length > 1 ? 's' : '') + ')</span></span><button class="' + toggleBtnClass + ' text-white text-xs px-2 py-0.5 rounded ml-2 font-normal" onclick="event.stopPropagation(); toggleCustomGroupType(\'' + key + '\', ' + toggleTarget + ')">' + toggleLabel + '</button></div>';
                groupHeader.onclick = (function(groupKey) {
                    return function() {
                        modalExpandedGroups[groupKey] = !modalExpandedGroups[groupKey];
                        renderCustomGroupsList();
                    };
                })(key);
                container.appendChild(groupHeader);

                // Group items (only show if expanded)
                if (isExpanded) {
                    var groupContainer = document.createElement('div');
                    groupContainer.className = 'border border-t-0 border-purple-600 rounded-b bg-gray-900 p-2 space-y-2 mb-2';

                    for (var j = 0; j < items.length; j++) {
                        var item = items[j];
                        var grp = item.group;
                        var idx = item.index;

                        var card = document.createElement('div');
                        card.className = 'bg-black border border-gray-700 rounded p-2 flex justify-between items-center hover:border-purple-500 transition-colors';

                        var info = document.createElement('div');
                        info.className = 'flex-1';
                        var enabledLabel = grp.enabled ? '<span class="text-green-400">✓ ENABLED</span>' : '<span class="text-gray-500">✗ DISABLED</span>';
                        info.innerHTML = '<div class="font-mono text-xs">' + enabledLabel + '</div>' +
                                        '<div class="text-[10px] text-gray-400 font-mono">B2: 0x' + (grp.b2_tail || '00').toUpperCase() + ' | B3: 0x' + (grp.b3 || '0000').toUpperCase() + ' | B4: 0x' + (grp.b4 || '0000').toUpperCase() + '</div>';

                        var actions = document.createElement('div');
                        actions.className = 'flex gap-1';

                    var editBtn = document.createElement('button');
                    editBtn.textContent = 'Edit';
                    editBtn.className = 'px-2 py-1 bg-blue-600 hover:bg-blue-500 rounded text-xs';
                    editBtn.onclick = (function(idx) { return function() { editCustomGroup(idx); }; })(idx);

                    var delBtn = document.createElement('button');
                    delBtn.textContent = 'Delete';
                    delBtn.className = 'px-2 py-1 bg-red-600 hover:bg-red-500 rounded text-xs';
                    delBtn.onclick = (function(idx) { return function() { deleteCustomGroup(idx); }; })(idx);

                        actions.appendChild(editBtn);
                        actions.appendChild(delBtn);
                        card.appendChild(info);
                        card.appendChild(actions);
                        groupContainer.appendChild(card);
                    }

                    container.appendChild(groupContainer);
                } else {
                    // Collapsed - just show thin line
                    var collapsedLine = document.createElement('div');
                    collapsedLine.className = 'border border-t-0 border-purple-600 rounded-b bg-gray-900 mb-2';
                    collapsedLine.style.height = '2px';
                    container.appendChild(collapsedLine);
                }
            }
        }

        function toggleCustomGroupType(typeKey, enabled) {
            for (var i = 0; i < customGroups.length; i++) {
                var grp = customGroups[i];
                var verLabel = grp.version == 1 ? 'B' : 'A';
                if (grp.type + verLabel === typeKey) {
                    customGroups[i].enabled = enabled;
                }
            }
            syncCustomGroups();
            renderCustomGroupsList();
            updateCustomGroupsDisplay();
        }

        function addCustomGroup() {
            document.getElementById('cg_edit_idx').value = '';
            document.getElementById('cg_enabled').checked = true;
            document.getElementById('cg_type').value = '0';
            document.getElementById('cg_version').value = '0';
            document.getElementById('cg_b2_tail').value = '00';
            document.getElementById('cg_b3').value = '0000';
            document.getElementById('cg_b4').value = '0000';
            document.getElementById('custom_groups_modal_title').textContent = 'Add Custom Group';
            document.getElementById('custom_group_edit_form').style.display = 'block';
        }

        function editCustomGroup(idx) {
            var grp = customGroups[idx];
            document.getElementById('cg_edit_idx').value = idx;
            document.getElementById('cg_enabled').checked = grp.enabled !== false;
            document.getElementById('cg_type').value = grp.type || 0;
            document.getElementById('cg_version').value = grp.version || 0;
            document.getElementById('cg_b2_tail').value = (grp.b2_tail || '00').toUpperCase();
            document.getElementById('cg_b3').value = (grp.b3 || '0000').toUpperCase();
            document.getElementById('cg_b4').value = (grp.b4 || '0000').toUpperCase();
            document.getElementById('custom_groups_modal_title').textContent = 'Edit Custom Group';
            document.getElementById('custom_group_edit_form').style.display = 'block';
        }

        function deleteCustomGroup(idx) {
            if (!confirm('Delete this custom group?')) return;
            customGroups.splice(idx, 1);
            syncCustomGroups();
            renderCustomGroupsList();
            updateCustomGroupsDisplay();
        }

        function cancelCustomGroupEdit() {
            document.getElementById('custom_group_edit_form').style.display = 'none';
            document.getElementById('custom_groups_modal_title').textContent = 'Manage Custom Groups';
        }

        function saveCustomGroup() {
            var idx = document.getElementById('cg_edit_idx').value;
            var grp = {
                enabled: document.getElementById('cg_enabled').checked,
                type: parseInt(document.getElementById('cg_type').value) || 0,
                version: parseInt(document.getElementById('cg_version').value) || 0,
                b2_tail: document.getElementById('cg_b2_tail').value.toUpperCase() || '00',
                b3: document.getElementById('cg_b3').value.toUpperCase() || '0000',
                b4: document.getElementById('cg_b4').value.toUpperCase() || '0000',
                schedule_freq: customGroups[idx] ? customGroups[idx].schedule_freq || 1 : 1
            };

            if (idx === '') {
                customGroups.push(grp);
            } else {
                customGroups[parseInt(idx)] = grp;
            }

            syncCustomGroups();
            renderCustomGroupsList();
            updateCustomGroupsDisplay();
            cancelCustomGroupEdit();
        }

        function syncCustomGroups() {
            var hiddenInput = document.getElementById('custom_groups');
            if (hiddenInput) {
                hiddenInput.value = JSON.stringify(customGroups);
            }
            socket.emit('update', { custom_groups: JSON.stringify(customGroups) });
        }

        var expandedGroups = {};

        function updateCustomGroupsDisplay() {
            var display = document.getElementById('custom_groups_display');
            if (!display) return;

            if (customGroups.length === 0) {
                display.innerHTML = 'No custom groups configured';
                display.className = 'text-xs text-gray-400';
                return;
            }

            // Group by type+version
            var grouped = {};
            for (var i = 0; i < customGroups.length; i++) {
                var grp = customGroups[i];
                var verLabel = grp.version == 1 ? 'B' : 'A';
                var key = grp.type + verLabel;
                if (!grouped[key]) {
                    grouped[key] = [];
                }
                grouped[key].push({index: i, group: grp});
            }

            // Sort group keys
            var keys = Object.keys(grouped).sort(function(a, b) {
                var aNum = parseInt(a);
                var bNum = parseInt(b);
                if (aNum !== bNum) return aNum - bNum;
                return a.localeCompare(b);
            });

            var html = '';
            for (var k = 0; k < keys.length; k++) {
                var key = keys[k];
                var items = grouped[key];
                var isExpanded = expandedGroups[key];
                var expandIcon = isExpanded ? '▼' : '▶';

                // Count enabled/disabled and get schedule frequency
                var enabledCount = 0;
                var scheduleFreq = 1;
                for (var j = 0; j < items.length; j++) {
                    if (items[j].group.enabled) enabledCount++;
                    // Use max schedule_freq from all items in this group
                    var itemFreq = items[j].group.schedule_freq || 1;
                    if (itemFreq > scheduleFreq) scheduleFreq = itemFreq;
                }

                // Group header (collapsible)
                html += '<div class="bg-gradient-to-r from-purple-900 to-purple-800 border border-purple-600 rounded-t px-3 py-2 cursor-pointer hover:from-purple-800 hover:to-purple-700 transition-colors" onclick="toggleCustomGroupExpand(\'' + key + '\')">';
                html += '<div class="flex items-center justify-between gap-2">';
                html += '<div class="font-mono text-sm font-bold text-purple-200 flex-shrink-0">';
                html += '<span class="inline-block w-4">' + expandIcon + '</span> Group ' + key;
                html += ' <span class="text-xs text-purple-300">(' + items.length + ' item' + (items.length > 1 ? 's' : '') + ', ' + enabledCount + ' enabled)</span>';
                html += '</div>';
                html += '<div class="flex items-center gap-2">';
                html += '<select onchange="event.stopPropagation(); updateGroupScheduleFreq(\'' + key + '\', this.value)" class="bg-black border border-purple-500 rounded px-2 py-1 text-xs text-purple-200 font-mono hover:bg-purple-950" title="Schedule frequency in Auto Mode">';
                html += '<option value="1"' + (scheduleFreq == 1 ? ' selected' : '') + '>1x</option>';
                html += '<option value="2"' + (scheduleFreq == 2 ? ' selected' : '') + '>2x</option>';
                html += '<option value="3"' + (scheduleFreq == 3 ? ' selected' : '') + '>3x</option>';
                html += '<option value="4"' + (scheduleFreq == 4 ? ' selected' : '') + '>4x</option>';
                html += '<option value="5"' + (scheduleFreq == 5 ? ' selected' : '') + '>5x</option>';
                html += '</select>';
                html += '<button onclick="event.stopPropagation(); deleteWholeGroup(\'' + key + '\')" class="px-2 py-1 bg-red-600 hover:bg-red-500 rounded text-xs text-white flex-shrink-0" title="Delete all Group ' + key + ' entries">';
                html += '🗑 Delete Group';
                html += '</button>';
                html += '</div>';
                html += '</div>';
                html += '</div>';

                // Group items (expandable)
                if (isExpanded) {
                    html += '<div class="border border-t-0 border-purple-600 rounded-b bg-gray-900 p-2 space-y-1 mb-2">';
                    for (var j = 0; j < items.length; j++) {
                        var item = items[j];
                        var grp = item.group;
                        var idx = item.index;
                        var enabledLabel = grp.enabled ? '<span class="text-green-400">✓</span>' : '<span class="text-gray-500">✗</span>';

                        html += '<div class="bg-black border border-gray-700 rounded p-2 flex justify-between items-center hover:border-purple-500 transition-colors">';
                        html += '<div class="flex-1 cursor-pointer" onclick="openCustomGroupsModalAndEdit(' + idx + ')">';
                        html += '<div class="font-mono text-xs">' + enabledLabel + ' B2: 0x' + (grp.b2_tail || '00').toUpperCase() + ' | B3: 0x' + (grp.b3 || '0000').toUpperCase() + ' | B4: 0x' + (grp.b4 || '0000').toUpperCase() + '</div>';
                        html += '</div>';
                        html += '<button onclick="event.stopPropagation(); deleteCustomGroupItem(' + idx + ')" class="px-2 py-1 bg-red-600 hover:bg-red-500 rounded text-xs ml-2" title="Delete this item">🗑</button>';
                        html += '</div>';
                    }
                    html += '</div>';
                } else {
                    html += '<div class="border border-t-0 border-purple-600 rounded-b bg-gray-900 mb-2"></div>';
                }
            }

            display.innerHTML = html;
            display.className = 'text-xs space-y-1';
        }

        function toggleCustomGroupExpand(groupKey) {
            expandedGroups[groupKey] = !expandedGroups[groupKey];
            updateCustomGroupsDisplay();
        }

        function updateGroupScheduleFreq(groupKey, freq) {
            var freqValue = parseInt(freq) || 1;

            // Update all items in this group
            for (var i = 0; i < customGroups.length; i++) {
                var grp = customGroups[i];
                var verLabel = grp.version == 1 ? 'B' : 'A';
                var key = grp.type + verLabel;
                if (key === groupKey) {
                    customGroups[i].schedule_freq = freqValue;
                }
            }

            syncCustomGroups();
            renderCustomGroupsList();
            updateCustomGroupsDisplay();
        }

        function deleteCustomGroupItem(idx) {
            if (!confirm('Delete this custom group entry?')) return;
            customGroups.splice(idx, 1);
            syncCustomGroups();
            renderCustomGroupsList();
            updateCustomGroupsDisplay();
        }

        function deleteWholeGroup(groupKey) {
            // Count items in this group
            var count = 0;
            for (var i = 0; i < customGroups.length; i++) {
                var grp = customGroups[i];
                var verLabel = grp.version == 1 ? 'B' : 'A';
                var key = grp.type + verLabel;
                if (key === groupKey) count++;
            }

            if (!confirm('Delete all ' + count + ' entries for Group ' + groupKey + '?')) return;

            // Remove all matching groups
            customGroups = customGroups.filter(function(grp) {
                var verLabel = grp.version == 1 ? 'B' : 'A';
                var key = grp.type + verLabel;
                return key !== groupKey;
            });

            syncCustomGroups();
            renderCustomGroupsList();
            updateCustomGroupsDisplay();
        }

        function openCustomGroupsModalAndEdit(idx) {
            openCustomGroupsModal();
            setTimeout(function() {
                editCustomGroup(idx);
            }, 100);
        }

        // Custom Groups Import/Export
        function exportCustomGroups() {
            var dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(customGroups, null, 2));
            var downloadAnchorNode = document.createElement('a');
            downloadAnchorNode.setAttribute("href", dataStr);
            downloadAnchorNode.setAttribute("download", "rds_custom_groups.json");
            document.body.appendChild(downloadAnchorNode);
            downloadAnchorNode.click();
            downloadAnchorNode.remove();
        }

        function showImportDialog() {
            document.getElementById('import_modal').style.display = 'flex';
        }

        function closeImportDialog() {
            document.getElementById('import_modal').style.display = 'none';
        }

        function updateImportMethod() {
            var method = document.getElementById('import_method').value;
            document.getElementById('import_text_section').style.display = method === 'text' ? 'block' : 'none';
            document.getElementById('import_url_section').style.display = method === 'url' ? 'block' : 'none';
            document.getElementById('import_json_section').style.display = method === 'json' ? 'block' : 'none';
            document.getElementById('import_rdsspy_section').style.display = method === 'rdsspy' ? 'block' : 'none';

            // Create group grid when RDS Spy is selected
            if (method === 'rdsspy') {
                createRdsSpyGroupGrid();
            }
        }

        var rdsspyParsedGroups = [];
        var rdsspyGroupFilter = {}; // Track which group types to import (default: all enabled)

        // Initialize group filter (all groups enabled by default)
        function initRdsSpyGroupFilter() {
            for (var type = 0; type <= 15; type++) {
                for (var ver = 0; ver <= 1; ver++) {
                    var verLabel = ver == 1 ? 'B' : 'A';
                    var key = type + verLabel;
                    rdsspyGroupFilter[key] = true;
                }
            }
        }
        initRdsSpyGroupFilter();

        // Create the group grid UI
        function createRdsSpyGroupGrid() {
            var grid = document.getElementById('rdsspy_group_grid');
            if (!grid) return;

            grid.innerHTML = '';

            for (var type = 0; type <= 15; type++) {
                for (var ver = 0; ver <= 1; ver++) {
                    var verLabel = ver == 1 ? 'B' : 'A';
                    var key = type + verLabel;

                    var box = document.createElement('div');
                    box.id = 'rdsspy_group_' + key;
                    box.className = 'rdsspy-group-box rdsspy-group-disabled';
                    box.setAttribute('data-group', key);
                    box.innerHTML = '<div class="font-mono text-xs font-bold">' + key + '</div>';
                    box.onclick = function() {
                        var groupKey = this.getAttribute('data-group');
                        if (this.classList.contains('rdsspy-group-detected')) {
                            rdsspyGroupFilter[groupKey] = !rdsspyGroupFilter[groupKey];
                            updateRdsSpyGroupGrid();
                        }
                    };

                    grid.appendChild(box);
                }
            }
        }

        // Select all detected RDS Spy groups
        function selectAllRdsSpyGroups() {
            for (var type = 0; type <= 15; type++) {
                for (var ver = 0; ver <= 1; ver++) {
                    var verLabel = ver == 1 ? 'B' : 'A';
                    var key = type + verLabel;
                    var box = document.getElementById('rdsspy_group_' + key);
                    if (box && box.classList.contains('rdsspy-group-detected')) {
                        rdsspyGroupFilter[key] = true;
                    }
                }
            }
            updateRdsSpyGroupGrid();
        }

        // Deselect all detected RDS Spy groups
        function deselectAllRdsSpyGroups() {
            for (var type = 0; type <= 15; type++) {
                for (var ver = 0; ver <= 1; ver++) {
                    var verLabel = ver == 1 ? 'B' : 'A';
                    var key = type + verLabel;
                    var box = document.getElementById('rdsspy_group_' + key);
                    if (box && box.classList.contains('rdsspy-group-detected')) {
                        rdsspyGroupFilter[key] = false;
                    }
                }
            }
            updateRdsSpyGroupGrid();
        }

        // Update the grid based on textarea content
        function updateRdsSpyGroupGrid() {
            var text = document.getElementById('import_rdsspy_data').value;
            if (!text.trim()) {
                // Reset grid if no text
                createRdsSpyGroupGrid();
                document.getElementById('rdsspy_enabled_count').textContent = '0 groups selected';
                return;
            }

            // Parse to detect which groups are present
            var detectedGroups = {};
            var lines = text.trim().split('\n');

            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (!line || line.startsWith('<') || line.startsWith('#')) continue;

                var parts = line.split(/\s+/);
                if (parts.length < 4) continue;

                try {
                    var b2_full = parseInt(parts[1], 16);
                    var group_type = (b2_full >> 12) & 0x0F;
                    var version = (b2_full >> 11) & 0x01;
                    var verLabel = version == 1 ? 'B' : 'A';
                    var key = group_type + verLabel;

                    detectedGroups[key] = (detectedGroups[key] || 0) + 1;
                } catch (e) {
                    // Skip invalid lines
                }
            }

            // Update each box
            for (var type = 0; type <= 15; type++) {
                for (var ver = 0; ver <= 1; ver++) {
                    var verLabel = ver == 1 ? 'B' : 'A';
                    var key = type + verLabel;
                    var box = document.getElementById('rdsspy_group_' + key);
                    if (!box) continue;

                    if (detectedGroups[key]) {
                        // Group detected in data
                        box.classList.remove('rdsspy-group-disabled');
                        box.classList.add('rdsspy-group-detected');

                        if (rdsspyGroupFilter[key]) {
                            box.classList.add('rdsspy-group-enabled');
                            box.classList.remove('rdsspy-group-toggled-off');
                        } else {
                            box.classList.add('rdsspy-group-toggled-off');
                            box.classList.remove('rdsspy-group-enabled');
                        }

                        box.innerHTML = '<div class="font-mono text-xs font-bold">' + key + '</div><div class="text-[10px]">(' + detectedGroups[key] + ')</div>';
                    } else {
                        // Group not in data
                        box.classList.remove('rdsspy-group-detected', 'rdsspy-group-enabled', 'rdsspy-group-toggled-off');
                        box.classList.add('rdsspy-group-disabled');
                        box.innerHTML = '<div class="font-mono text-xs font-bold">' + key + '</div>';
                    }
                }
            }

            // Count enabled groups
            var enabledCount = 0;
            for (var key in detectedGroups) {
                if (rdsspyGroupFilter[key]) enabledCount++;
            }
            document.getElementById('rdsspy_enabled_count').textContent = enabledCount + ' group' + (enabledCount !== 1 ? 's' : '') + ' selected';
        }

        function doImport() {
            var method = document.getElementById('import_method').value;
            var mode = document.querySelector('input[name="import_mode"]:checked').value;

            var payload = {
                type: method,
                mode: mode
            };

            if (method === 'text') {
                payload.text = document.getElementById('import_text_data').value;
                payload.default_group_type = parseInt(document.getElementById('import_default_type').value);
                payload.default_version = parseInt(document.getElementById('import_default_version').value);
            } else if (method === 'url') {
                payload.url = document.getElementById('import_url').value;
                payload.default_group_type = parseInt(document.getElementById('import_url_default_type').value);
                payload.default_version = parseInt(document.getElementById('import_url_default_version').value);
            } else if (method === 'json') {
                try {
                    payload.custom_groups = JSON.parse(document.getElementById('import_json_data').value);
                } catch (e) {
                    alert('Invalid JSON: ' + e.message);
                    return;
                }
            } else if (method === 'rdsspy') {
                // Parse RDS Spy format and filter by enabled groups
                var text = document.getElementById('import_rdsspy_data').value;
                if (!text.trim()) {
                    alert('Please paste RDS Spy log data');
                    return;
                }

                var groups = [];
                var lines = text.trim().split('\n');

                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i].trim();
                    if (!line || line.startsWith('<') || line.startsWith('#')) continue;

                    var parts = line.split(/\s+/);
                    if (parts.length < 4) continue;

                    try {
                        var b2_full = parseInt(parts[1], 16);
                        var group_type = (b2_full >> 12) & 0x0F;
                        var version = (b2_full >> 11) & 0x01;
                        var b2_tail = b2_full & 0x1F;
                        var verLabel = version == 1 ? 'B' : 'A';
                        var key = group_type + verLabel;

                        // Only include if group type is enabled in filter
                        if (rdsspyGroupFilter[key]) {
                            groups.push({
                                type: group_type,
                                version: version,
                                b2_tail: ('0' + b2_tail.toString(16)).slice(-2).toUpperCase(),
                                b3: parts[2].toUpperCase(),
                                b4: parts[3].toUpperCase(),
                                enabled: true
                            });
                        }
                    } catch (e) {
                        // Skip invalid lines
                    }
                }

                console.log('RDS Spy import: Parsed ' + groups.length + ' groups from ' + lines.length + ' lines');
                console.log('Filter state:', rdsspyGroupFilter);
                console.log('Sample groups:', groups.slice(0, 3)); // Show first 3 groups

                if (groups.length === 0) {
                    alert('No groups selected for import.\n\nMake sure:\n1. You pasted valid RDS Spy log data\n2. At least one group is enabled (purple/glowing)\n3. Click "Select All" to enable all detected groups');
                    return;
                }

                // Frontend already parsed, send as JSON type so backend reads from custom_groups field
                payload.type = 'json';
                payload.custom_groups = groups;
                console.log('Sending to backend:', {type: payload.type, mode: payload.mode, count: groups.length});
            }

            fetch('/custom_groups/import', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload)
            })
            .then(response => response.json())
            .then(data => {
                console.log('Backend response:', data);
                if (data.success) {
                    alert('Imported ' + data.count + ' custom group(s)');
                    loadCustomGroups();
                    renderCustomGroupsList();
                    updateCustomGroupsDisplay();
                    closeImportDialog();
                } else {
                    alert('Import failed: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(error => {
                alert('Import failed: ' + error);
            });
        }

        function parseAndPreviewRdsSpy(text) {
            // Parse RDS Spy format client-side
            var lines = text.trim().split('\n');
            rdsspyParsedGroups = [];

            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (!line || line.startsWith('<') || line.startsWith('#')) continue;

                var parts = line.split(/\s+/);
                if (parts.length < 4) continue;

                try {
                    var b2_full = parseInt(parts[1], 16);
                    var group_type = (b2_full >> 12) & 0x0F;
                    var version = (b2_full >> 11) & 0x01;
                    var b2_tail = b2_full & 0x1F;

                    rdsspyParsedGroups.push({
                        type: group_type,
                        version: version,
                        b2_tail: ('0' + b2_tail.toString(16)).slice(-2).toUpperCase(),
                        b3: parts[2].toUpperCase(),
                        b4: parts[3].toUpperCase(),
                        enabled: true,
                        selected: true
                    });
                } catch (e) {
                    // Skip invalid lines
                }
            }

            if (rdsspyParsedGroups.length === 0) {
                alert('No valid RDS groups found in the input');
                return;
            }

            // Show preview modal
            renderRdsSpyPreview();
            closeImportDialog();
            document.getElementById('rdsspy_preview_modal').style.display = 'flex';
        }

        function renderRdsSpyPreview() {
            var container = document.getElementById('rdsspy_preview_list');
            container.innerHTML = '';

            // Group by type+version
            var grouped = {};
            for (var i = 0; i < rdsspyParsedGroups.length; i++) {
                var grp = rdsspyParsedGroups[i];
                var verLabel = grp.version == 1 ? 'B' : 'A';
                var key = grp.type + verLabel;
                if (!grouped[key]) {
                    grouped[key] = [];
                }
                grouped[key].push({index: i, group: grp});
            }

            // Sort group keys
            var keys = Object.keys(grouped).sort(function(a, b) {
                var aNum = parseInt(a);
                var bNum = parseInt(b);
                if (aNum !== bNum) return aNum - bNum;
                return a.localeCompare(b);
            });

            // Render each group type
            for (var k = 0; k < keys.length; k++) {
                var key = keys[k];
                var items = grouped[key];

                // Count selected items in this group
                var selectedCount = 0;
                for (var j = 0; j < items.length; j++) {
                    if (items[j].group.selected) selectedCount++;
                }
                var allSelected = selectedCount === items.length;

                // Group header
                var groupHeader = document.createElement('div');
                groupHeader.className = 'bg-gradient-to-r from-purple-900 to-purple-800 border border-purple-600 rounded-t px-3 py-2 mt-3 first:mt-0 flex items-center justify-between cursor-pointer hover:from-purple-800 hover:to-purple-700 transition-colors';
                groupHeader.onclick = (function(groupKey, currentlyAllSelected) {
                    return function(e) {
                        toggleRdsSpyGroup(groupKey, !currentlyAllSelected);
                    };
                })(key, allSelected);

                var headerLeft = document.createElement('div');
                headerLeft.innerHTML = '<span class="font-mono text-sm font-bold text-purple-200">Group ' + key + '</span> ' +
                    '<span class="text-xs text-purple-300">(' + selectedCount + '/' + items.length + ' selected)</span>';

                var headerToggle = document.createElement('div');
                headerToggle.className = 'px-3 py-1 rounded text-xs font-bold ' + (allSelected ? 'bg-green-600 text-white' : 'bg-gray-700 text-gray-300');
                headerToggle.innerHTML = allSelected ? 'All Selected' : 'Some/None';

                groupHeader.appendChild(headerLeft);
                groupHeader.appendChild(headerToggle);
                container.appendChild(groupHeader);

                // Group items
                var groupContainer = document.createElement('div');
                groupContainer.className = 'border border-t-0 border-purple-600 rounded-b bg-gray-900 p-2 space-y-1';

                for (var j = 0; j < items.length; j++) {
                    var item = items[j];
                    var grp = item.group;
                    var idx = item.index;

                    var card = document.createElement('div');
                    // Visual feedback: bright when selected, dark/dim when not selected
                    if (grp.selected) {
                        card.className = 'bg-gradient-to-r from-purple-900 to-purple-800 border-2 border-purple-400 rounded p-3 flex justify-between items-center text-xs cursor-pointer hover:from-purple-800 hover:to-purple-700 transition-all shadow-lg shadow-purple-900/50';
                    } else {
                        card.className = 'bg-gray-950 border border-gray-800 rounded p-3 flex justify-between items-center text-xs cursor-pointer hover:border-gray-600 transition-all opacity-40';
                    }

                    // Make entire card clickable
                    card.onclick = (function(index) {
                        return function(e) {
                            rdsspyParsedGroups[index].selected = !rdsspyParsedGroups[index].selected;
                            renderRdsSpyPreview();
                        };
                    })(idx);

                    var info = document.createElement('div');
                    info.className = 'flex-1 font-mono ' + (grp.selected ? 'text-purple-100 font-semibold' : 'text-gray-600');
                    info.innerHTML = 'B2: 0x' + grp.b2_tail + ' | B3: 0x' + grp.b3 + ' | B4: 0x' + grp.b4;

                    var checkIcon = document.createElement('div');
                    checkIcon.className = 'flex-shrink-0 w-6 h-6 rounded flex items-center justify-center ' + (grp.selected ? 'bg-green-500 text-white' : 'bg-gray-800 text-gray-600');
                    checkIcon.innerHTML = grp.selected ? '✓' : '✗';

                    card.appendChild(info);
                    card.appendChild(checkIcon);
                    groupContainer.appendChild(card);
                }

                container.appendChild(groupContainer);
            }
        }

        function toggleRdsSpyGroup(groupKey, checked) {
            for (var i = 0; i < rdsspyParsedGroups.length; i++) {
                var grp = rdsspyParsedGroups[i];
                var verLabel = grp.version == 1 ? 'B' : 'A';
                var key = grp.type + verLabel;
                if (key === groupKey) {
                    grp.selected = checked;
                }
            }
            renderRdsSpyPreview();
        }

        function rdsspySelectAll() {
            for (var i = 0; i < rdsspyParsedGroups.length; i++) {
                rdsspyParsedGroups[i].selected = true;
            }
            renderRdsSpyPreview();
        }

        function rdsspyDeselectAll() {
            for (var i = 0; i < rdsspyParsedGroups.length; i++) {
                rdsspyParsedGroups[i].selected = false;
            }
            renderRdsSpyPreview();
        }

        function closeRdsSpyPreview() {
            document.getElementById('rdsspy_preview_modal').style.display = 'none';
            showImportDialog();
        }

        function confirmRdsSpyImport() {
            var selectedGroups = rdsspyParsedGroups.filter(function(g) { return g.selected; });

            if (selectedGroups.length === 0) {
                alert('Please select at least one group to import');
                return;
            }

            // Remove selected flag before sending
            var cleanGroups = selectedGroups.map(function(g) {
                return {
                    type: g.type,
                    version: g.version,
                    b2_tail: g.b2_tail,
                    b3: g.b3,
                    b4: g.b4,
                    enabled: g.enabled
                };
            });

            var mode = document.querySelector('input[name="rdsspy_import_mode"]:checked').value;

            var payload = {
                type: 'json',
                mode: mode,
                custom_groups: cleanGroups
            };

            fetch('/custom_groups/import', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload)
            })
            .then(response => response.json())
            .then(data => {
                console.log('New Preview: Backend response for confirmRdsSpyImport:', data);
                if (data.success) {
                    alert('Imported ' + selectedGroups.length + ' custom group(s)');
                    loadCustomGroups();
                    renderCustomGroupsList();
                    updateCustomGroupsDisplay();
                    document.getElementById('rdsspy_preview_modal').style.display = 'none';
                } else {
                    alert('Import failed: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(error => {
                alert('Import failed: ' + error);
            });
        }

        function togglePower() {
            running = !running;
            updatePwr();
            if(running) {
                socket.emit('control', {
                    action: 'start',
                    dev_in: document.getElementById('dev_in').value,
                    dev_out: document.getElementById('dev_out').value
                });
            } else {
                socket.emit('control', { action: 'stop' });
            }
        }

        function manualSave() {
            // Force a sync to ensure current state is saved
            const saveBtn = document.getElementById('saveBtn');
            const originalText = saveBtn.innerHTML;

            // Show confirmation message
            saveBtn.innerHTML = '✓ Saved!';
            saveBtn.style.background = '#059669';
            saveBtn.style.borderColor = '#047857';

            setTimeout(() => {
                saveBtn.innerHTML = originalText;
                saveBtn.style.background = '';
                saveBtn.style.borderColor = '';
                saveBtn.classList.remove('has-changes');
                // Reset sync counter to prevent button from showing immediately
                window.syncCount = 1;
            }, 1500);

            sync();
        }

        // Initialize cycle controls on load
        updateCycleControls();

        setInterval(() => {
             let hasInput = document.getElementById('dev_in').value != "-1";
             let gl = document.getElementById('genlock');
             if(!hasInput) {
                 if(!gl.disabled) { gl.checked = false; gl.disabled = true; gl.parentElement.classList.add('opacity-50'); sync(); }
             } else {
                 if(gl.disabled) { gl.disabled = false; gl.parentElement.classList.remove('opacity-50'); }
             }

             // Disable pilot slider when pass-through is active and an input device is present
             const pt = document.getElementById('passthrough');
             const pilotSlider = document.getElementById('pilot_level');
             const pilotStatus = document.getElementById('pilot_status');
             if (pilotSlider && pt) {
                 const pilotDisabled = hasInput && pt.checked;
                 if (pilotDisabled) {
                     if (!pilotSlider.disabled) { pilotSlider.disabled = true; pilotSlider.parentElement.classList.add('opacity-50'); if (pilotStatus) pilotStatus.innerText = 'Disabled (Pass-through)'; }
                 } else {
                     if (pilotSlider.disabled) { pilotSlider.disabled = false; pilotSlider.parentElement.classList.remove('opacity-50'); if (pilotStatus) pilotStatus.innerText = 'Enabled'; }
                 }
             }

               // Update cycle controls visibility regularly
               updateCycleControls();
        }, 1000);

        // Initialize RT Messages on page load
        initRTMsgTagSelects();
        renderRTMessages();
        updatePTYList(); // Initialize PTY list based on RDS/RBDS setting
        updateAFMethodUI(); // Initialize AF UI based on method
        loadDatasets(); // Initialize datasets on page load
        updateRDS2Visibility(); // Initialize RDS2 carrier visibility
        loadEONServices(); // Initialize EON services on page load
        loadCustomODAList(); // Initialize custom ODA list on page load
        loadCustomGroups(); // Initialize custom groups on page load
        updateCustomGroupsDisplay(); // Update custom groups display
        loadDynamicControlRules(); // Initialize dynamic control rules on page load
        updateDynamicControlDisplay(); // Update dynamic control display

        // === AF PAIR FUNCTIONS ===
        var afPairs = [];
        var pendingNewAFPair = null;

        function updateAFMethodUI() {
            var method = document.getElementById('af_method').value;
            var methodAUI = document.getElementById('af_method_a_ui');
            var methodBUI = document.getElementById('af_method_b_ui');

            if (method === 'A') {
                methodAUI.style.display = 'block';
                methodBUI.style.display = 'none';
            } else {
                methodAUI.style.display = 'none';
                methodBUI.style.display = 'block';
                parseAFPairs(); // Load pairs from af_list
            }
        }

        function parseAFPairs() {
            // Load AF pairs from state
            try {
                var pairsJson = '{{ state.af_pairs|safe }}';
                afPairs = JSON.parse(pairsJson) || [];
            } catch(e) {
                // Fallback: parse af_list into a single pair for backward compatibility
                var afList = document.getElementById('af_list').value;
                if (afList) {
                    var freqs = afList.split(',').map(function(f) { return f.trim(); }).filter(function(f) { return f; });
                    if (freqs.length > 0) {
                        afPairs = [{
                            id: 'pair_1',
                            main: freqs[0],
                            alts: freqs.slice(1).join(', ')
                        }];
                    }
                }
            }
            renderAFPairs();
        }

        function renderAFPairs() {
            var container = document.getElementById('af_pairs_list');
            var emptyState = document.getElementById('af_pairs_empty');

            if (!afPairs || afPairs.length === 0) {
                container.innerHTML = '';
                emptyState.style.display = 'block';
                return;
            }

            emptyState.style.display = 'none';
            container.innerHTML = '';

            afPairs.forEach(function(pair, index) {
                var card = document.createElement('div');
                card.className = 'rt-msg-card';
                card.setAttribute('data-id', pair.id);

                var header = document.createElement('div');
                header.className = 'rt-msg-header';
                header.style.cssText = 'display: flex; align-items: center;';

                var editArea = document.createElement('div');
                editArea.style.cssText = 'cursor:pointer; flex: 1; display: flex; align-items: center;';
                editArea.onclick = function() { editAFPair(pair.id); };

                var pairLabel = document.createElement('span');
                pairLabel.className = 'text-sm font-bold text-[#7c3aed]';
                pairLabel.textContent = 'Pair ' + (index + 1) + ': ' + pair.main + ' MHz';

                var arrow = document.createElement('span');
                arrow.className = 'text-xs text-gray-500 mx-2';
                arrow.textContent = '→';

                var altsLabel = document.createElement('span');
                altsLabel.className = 'text-xs text-gray-400';
                altsLabel.textContent = pair.alts || '(no alternates)';

                editArea.appendChild(pairLabel);
                editArea.appendChild(arrow);
                editArea.appendChild(altsLabel);

                if (pair.regional) {
                    var rvBadge = document.createElement('span');
                    rvBadge.className = 'text-[10px] bg-orange-600 text-white px-2 py-0.5 rounded ml-2';
                    rvBadge.textContent = 'RV';
                    editArea.appendChild(rvBadge);
                }

                var deleteBtn = document.createElement('button');
                deleteBtn.className = 'text-sm hover:text-red-400 ml-2';
                deleteBtn.style.cssText = 'flex-shrink: 0;';
                deleteBtn.textContent = '×';
                deleteBtn.onclick = function(e) {
                    e.stopPropagation();
                    deleteAFPair(pair.id);
                };

                header.appendChild(editArea);
                header.appendChild(deleteBtn);
                card.appendChild(header);
                container.appendChild(card);
            });
        }

        function addAFPair() {
            pendingNewAFPair = {
                id: 'pair_' + Date.now(),
                main: '',
                alts: '',
                regional: false
            };
            openAFPairModal(pendingNewAFPair, true);
        }

        function editAFPair(id) {
            var pair = afPairs.find(function(p) { return p.id === id; });
            if (!pair) return;
            pendingNewAFPair = null;
            openAFPairModal(pair, false);
        }

        function openAFPairModal(pair, isNew) {
            document.getElementById('af_pair_edit_id').value = pair.id;
            document.getElementById('af_pair_modal_title').textContent = isNew ? 'Add Frequency Pair' : 'Edit Frequency Pair';
            document.getElementById('af_pair_main').value = pair.main || '';
            document.getElementById('af_pair_alts').value = pair.alts || '';
            document.getElementById('af_pair_regional').checked = pair.regional || false;
            document.getElementById('af_pair_modal').style.display = 'flex';
        }

        function closeAFPairModal() {
            pendingNewAFPair = null;
            document.getElementById('af_pair_modal').style.display = 'none';
        }

        function saveAFPair() {
            var id = document.getElementById('af_pair_edit_id').value;
            var pair = afPairs.find(function(p) { return p.id === id; });

            if (!pair && pendingNewAFPair && pendingNewAFPair.id === id) {
                pair = pendingNewAFPair;
                afPairs.push(pair);
                pendingNewAFPair = null;
            }

            if (!pair) return;

            pair.main = document.getElementById('af_pair_main').value.trim();
            pair.alts = document.getElementById('af_pair_alts').value.trim();
            pair.regional = document.getElementById('af_pair_regional').checked;

            closeAFPairModal();
            renderAFPairs();
            syncAFPairs();
        }

        function deleteAFPair(id) {
            afPairs = afPairs.filter(function(p) { return p.id !== id; });
            renderAFPairs();
            syncAFPairs();
        }

        function syncAFPairs() {
            // Store pairs as JSON in af_pairs field
            socket.emit('update', { af_pairs: JSON.stringify(afPairs) });

            // Build af_list for display - show all unique main frequencies
            if (afPairs.length === 0) {
                document.getElementById('af_list').value = '';
            } else {
                // Collect all unique frequencies from all pairs
                var allFreqs = [];
                var seen = {};

                afPairs.forEach(function(pair) {
                    if (pair.main && !seen[pair.main]) {
                        allFreqs.push(pair.main);
                        seen[pair.main] = true;
                    }
                    if (pair.alts) {
                        var altFreqs = pair.alts.split(',').map(function(f) { return f.trim(); }).filter(function(f) { return f; });
                        altFreqs.forEach(function(alt) {
                            if (!seen[alt]) {
                                allFreqs.push(alt);
                                seen[alt] = true;
                            }
                        });
                    }
                });

                document.getElementById('af_list').value = allFreqs.join(', ');
            }
        }

        // ============= ENHANCED RADIOTEXT (eRT) MESSAGE MANAGEMENT =============
        var ertMessages = [];
        var pendingNewERTMessage = null;

        // Load eRT messages from state
        try {
            var ertMessagesStr = {{ state.ert_messages|tojson }};
            if (ertMessagesStr && ertMessagesStr !== "[]") {
                ertMessages = JSON.parse(ertMessagesStr);
            }
        } catch (e) {
            console.error('Failed to parse eRT messages:', e);
            ertMessages = [];
        }

        function updateERTModeUI() {
            var mode = document.querySelector('input[name="ert_message_mode"]:checked').value;
            var manualUI = document.getElementById('ert_manual_ui');
            var builderUI = document.getElementById('ert_builder_ui');
            
            if (mode === 'manual') {
                manualUI.style.display = 'block';
                builderUI.style.display = 'none';
            } else {
                manualUI.style.display = 'none';
                builderUI.style.display = 'block';
                renderERTMessages();
            }
        }

        function renderERTMessages() {
            var container = document.getElementById('ert_messages_list');
            var empty = document.getElementById('ert_messages_empty');
            
            if (!container || !empty) return;
            
            if (ertMessages.length === 0) {
                container.innerHTML = '';
                empty.style.display = 'block';
                return;
            }
            
            empty.style.display = 'none';
            var html = '';
            
            for (var i = 0; i < ertMessages.length; i++) {
                var msg = ertMessages[i];
                var enabled = msg.enabled !== false; // Default to enabled
                
                // Handle content more robustly
                var rawContent = msg.content;
                if (rawContent == null || rawContent == undefined || rawContent === 'null' || rawContent === 'undefined') {
                    rawContent = '';
                }
                var preview = String(rawContent).substring(0, 60);
                if (String(rawContent).length > 60) preview += '...';
                
                // Debug log to help identify issues
                console.log('eRT Message:', msg.id, 'Raw Content:', msg.content, 'Processed Content:', rawContent, 'Preview:', preview);
                
                var sourceIcon = '';
                switch (msg.source_type) {
                    case 'manual': sourceIcon = '✏️'; break;
                    case 'file': sourceIcon = '📁'; break;
                    case 'url': sourceIcon = '🌐'; break;
                    default: sourceIcon = '📝'; break;
                }
                
                html += '<div class="bg-[#1a1a1a] border border-[#333] rounded p-2 flex justify-between items-center cursor-pointer hover:bg-[#222]" onclick="editERTMessage(\'' + msg.id + '\')">';
                html += '<div class="flex-1 min-w-0">';
                html += '<div class="flex items-center gap-2">';
                html += '<span>' + sourceIcon + '</span>';
                html += '<span class="text-sm font-medium text-white">' + (msg.source_type || 'manual').toUpperCase() + '</span>';
                html += '<span class="text-xs text-gray-400">(' + (msg.cycles || 2) + ' cycles)</span>';
                if (msg.rt_plus_enabled) html += '<span class="text-xs bg-orange-900 text-orange-200 px-2 py-1 rounded">RT+</span>';
                if (!enabled) html += '<span class="text-xs bg-red-900 text-red-200 px-2 py-1 rounded">DISABLED</span>';
                html += '</div>';
                if (preview) {
                    html += '<div class="text-xs text-gray-400 mt-1 truncate">' + escapeHtml(preview) + '</div>';
                }
                html += '</div>';
                html += '<div class="flex gap-1">';
                html += '<button onclick="event.stopPropagation(); toggleERTMessage(\'' + msg.id + '\')" title="' + (enabled ? 'Disable' : 'Enable') + '" class="px-2 py-1 text-xs ' + (enabled ? 'bg-green-800 text-green-200' : 'bg-gray-800 text-gray-400') + ' rounded">●</button>';
                html += '<button onclick="event.stopPropagation(); deleteERTMessage(\'' + msg.id + '\')" title="Delete" class="px-2 py-1 text-xs bg-red-800 text-red-200 rounded hover:bg-red-700">×</button>';
                html += '</div>';
                html += '</div>';
            }
            
            container.innerHTML = html;
        }

        function addERTMessage() {
            var newMsg = {
                id: 'ert_msg_' + Date.now(),
                cycles: 2,
                source_type: 'manual',
                content: '',
                enabled: true
            };
            
            pendingNewERTMessage = newMsg;
            editERTMessage(newMsg.id);
        }

        function editERTMessage(id) {
            var msg = ertMessages.find(function(m) { return m.id === id; });
            if (!msg && pendingNewERTMessage && pendingNewERTMessage.id === id) {
                msg = pendingNewERTMessage;
            }
            
            if (!msg) return;
            
            // Populate form
            document.getElementById('ert_msg_edit_id').value = id;
            document.getElementById('ert_msg_cycles').value = msg.cycles || 2;
            document.getElementById('ert_msg_enabled').checked = msg.enabled !== false;

            // Load text trim flags
            var etEl = document.getElementById('ert_msg_trim_parens'); if (etEl) etEl.checked = msg.trim_parens || false;
            etEl = document.getElementById('ert_msg_trim_brackets'); if (etEl) etEl.checked = msg.trim_brackets || false;
            etEl = document.getElementById('ert_msg_trim_semicolon'); if (etEl) etEl.checked = msg.trim_at_semicolon || false;
            etEl = document.getElementById('ert_msg_trim_maxlen'); if (etEl) etEl.checked = msg.trim_max_len || false;

            // Set source type
            var sourceRadios = document.querySelectorAll('input[name="ert_msg_source"]');
            sourceRadios.forEach(function(radio) {
                radio.checked = radio.value === (msg.source_type || 'manual');
            });
            
            // Set content based on source type
            if (msg.source_type === 'manual') {
                document.getElementById('ert_msg_text').value = msg.content || '';
            } else {
                document.getElementById('ert_msg_content').value = msg.content || '';
            }
            
            // Set RT+ configuration
            document.getElementById('ert_msg_rtplus_enabled').checked = msg.rt_plus_enabled || false;

            // Load tagging policies
            ertTaggingPolicies = [];
            try {
                if (msg.tagging_policies) {
                    ertTaggingPolicies = JSON.parse(msg.tagging_policies) || [];
                }
            } catch(e) {}

            // Reset sample text
            ertGlobalSampleText = msg.sample_text || 'Artist - Song Title';
            var sampleEl = document.getElementById('ert_msg_sample_text');
            if (sampleEl) sampleEl.value = ertGlobalSampleText;

            updateERTMsgSourceUI();
            updateERTRTPlusUI();
            updateERTMsgPreview();
            
            document.getElementById('ert_msg_modal_title').textContent = pendingNewERTMessage ? 'Add eRT Message' : 'Edit eRT Message';
            document.getElementById('ert_msg_modal').style.display = 'flex';
        }

        function updateERTMsgSourceUI() {
            var sourceType = document.querySelector('input[name="ert_msg_source"]:checked').value;
            var manualContent = document.getElementById('ert_msg_manual_content');
            var externalContent = document.getElementById('ert_msg_external_content');
            var contentLabel = document.getElementById('ert_content_label');
            var contentHint = document.getElementById('ert_content_hint');
            
            if (sourceType === 'manual') {
                manualContent.style.display = 'block';
                externalContent.style.display = 'none';
            } else {
                manualContent.style.display = 'none';
                externalContent.style.display = 'block';
                
                if (sourceType === 'file') {
                    contentLabel.textContent = 'File Path';
                    contentHint.textContent = 'Path to text file containing eRT content';
                    document.getElementById('ert_msg_content').placeholder = '/path/to/file.txt';
                } else if (sourceType === 'url') {
                    contentLabel.textContent = 'URL';
                    contentHint.textContent = 'URL to fetch eRT content from';
                    document.getElementById('ert_msg_content').placeholder = 'https://example.com/api/text';
                }
            }
            updateERTMsgPreview();
        }

        function updateERTMsgPreview() {
            var sourceType = document.querySelector('input[name="ert_msg_source"]:checked') ? document.querySelector('input[name="ert_msg_source"]:checked').value : 'manual';
            var previewDiv = document.getElementById('ert_msg_preview');
            
            if (!previewDiv) return;
            
            var content = '';
            
            if (sourceType === 'manual') {
                content = document.getElementById('ert_msg_text').value || '';
            } else if (sourceType === 'file') {
                var filePath = document.getElementById('ert_msg_content').value;
                if (filePath) {
                    // Show loading indicator and fetch file content
                    previewDiv.innerHTML = '<div class="text-xs text-yellow-400">📁 Loading file content...</div>';
                    fetchERTFileContent(filePath);
                    return;
                } else {
                    content = '';
                }
            } else if (sourceType === 'url') {
                var url = document.getElementById('ert_msg_content').value;
                if (url) {
                    previewDiv.innerHTML = '<div class="text-xs text-yellow-400">🌐 Loading URL content...</div>';
                    fetchERTURLContent(url);
                    return;
                } else {
                    content = '';
                }
            }
            
            if (content) {
                var truncated = content.length > 100 ? content.substring(0, 100) + '...' : content;
                previewDiv.innerHTML = '<div class="text-xs text-green-400">' + escapeHtml(truncated) + '</div>';
            } else {
                previewDiv.innerHTML = '<div class="text-xs text-gray-500">No content to preview</div>';
            }
        }

        function fetchERTFileContent(filePath) {
            fetch('/resolve-content', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    source_type: 'file', 
                    content: filePath 
                })
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var previewDiv = document.getElementById('ert_msg_preview');
                if (previewDiv) {
                    if (data.ok) {
                        var content = data.resolved || '';
                        var truncated = content.length > 100 ? content.substring(0, 100) + '...' : content;
                        previewDiv.innerHTML = '<div class="text-xs text-green-400">' + escapeHtml(truncated) + '</div>';
                    } else {
                        previewDiv.innerHTML = '<div class="text-xs text-red-400">❌ ' + (data.error || 'Failed to load file') + '</div>';
                    }
                }
            })
            .catch(function(e) {
                var previewDiv = document.getElementById('ert_msg_preview');
                if (previewDiv) {
                    previewDiv.innerHTML = '<div class="text-xs text-red-400">❌ Error loading file</div>';
                }
            });
        }

        function fetchERTURLContent(url) {
            fetch('/resolve-content', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    source_type: 'url', 
                    content: url 
                })
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var previewDiv = document.getElementById('ert_msg_preview');
                if (previewDiv) {
                    if (data.ok) {
                        var content = data.resolved || '';
                        var truncated = content.length > 100 ? content.substring(0, 100) + '...' : content;
                        previewDiv.innerHTML = '<div class="text-xs text-green-400">' + escapeHtml(truncated) + '</div>';
                    } else {
                        previewDiv.innerHTML = '<div class="text-xs text-red-400">❌ ' + (data.error || 'Failed to load URL') + '</div>';
                    }
                }
            })
            .catch(function(e) {
                var previewDiv = document.getElementById('ert_msg_preview');
                if (previewDiv) {
                    previewDiv.innerHTML = '<div class="text-xs text-red-400">❌ Error loading URL</div>';
                }
            });
        }

        // ============= eRT TAGGING POLICIES =============
        var ertTaggingPolicies = [];
        var ertCurrentEditingPolicyId = null;
        var ertGlobalSampleText = 'Artist - Song Title';

        function updateERTSampleText() {
            var el = document.getElementById('ert_msg_sample_text');
            if (el) ertGlobalSampleText = el.value || 'Artist - Song Title';
        }

        function updateERTRTPlusUI() {
            var enabled = document.getElementById('ert_msg_rtplus_enabled').checked;
            var options = document.getElementById('ert_msg_rtplus_options');
            if (options) options.style.display = enabled ? 'block' : 'none';
            // Show sample text input only for manual source mode
            var sampleWrap = document.getElementById('ert_msg_rtplus_sample_wrap');
            if (sampleWrap) {
                var srcEl = document.querySelector('input[name="ert_msg_source"]:checked');
                var isManual = !srcEl || srcEl.value === 'manual';
                sampleWrap.style.display = (enabled && isManual) ? 'block' : 'none';
            }
            if (enabled) {
                renderERTTaggingPolicies();
                updateERTRTPlusPreview();
            }
        }

        function updateERTRTPlusPreview() {
            var previewEl = document.getElementById('ert_rtplus_preview');
            var tag1Info = document.getElementById('ert_msg_tag1_info');
            var tag2Info = document.getElementById('ert_msg_tag2_info');
            var countEl = document.getElementById('ert_msg_char_count');
            var ruleEl = document.getElementById('ert_msg_rule_applied');
            if (!previewEl) return;

            // Get sample text
            var srcEl = document.querySelector('input[name="ert_msg_source"]:checked');
            var isManual = !srcEl || srcEl.value === 'manual';
            var sampleInput = document.getElementById('ert_msg_sample_text');
            var content = isManual
                ? (sampleInput && sampleInput.value ? sampleInput.value : ertGlobalSampleText)
                : (document.getElementById('ert_msg_content') ? document.getElementById('ert_msg_content').value || ertGlobalSampleText : ertGlobalSampleText);

            var result = applyERTTaggingPolicies(content);
            var preview = result.text || content;
            var limit = 128;

            // Character count
            if (countEl) {
                countEl.textContent = preview.length;
                countEl.className = preview.length > limit ? 'text-red-400' : (preview.length > 100 ? 'text-yellow-400' : 'text-green-400');
            }

            // Applied policy label
            if (ruleEl) {
                if (result.appliedPolicy) {
                    ruleEl.textContent = 'Current policy: ' + result.appliedPolicy;
                    ruleEl.className = 'text-blue-400 text-xs';
                } else {
                    ruleEl.textContent = 'Current policy: –';
                    ruleEl.className = 'text-gray-400 text-xs';
                }
            }

            // Build highlighted preview HTML
            var t1s = result.tag1Start, t1l = result.tag1Len, t2s = result.tag2Start, t2l = result.tag2Len;
            var html = '';
            var rtpEnabled = document.getElementById('ert_msg_rtplus_enabled') && document.getElementById('ert_msg_rtplus_enabled').checked;
            if (rtpEnabled && (t1l > 0 || t2l > 0)) {
                for (var i = 0; i < preview.length; i++) {
                    var ch = escapeHtml(preview[i]);
                    if (t1l > 0 && i >= t1s && i < t1s + t1l) {
                        html += '<span class="rtplus-tag-1">' + ch + '</span>';
                    } else if (t2l > 0 && i >= t2s && i < t2s + t2l) {
                        html += '<span class="rtplus-tag-2">' + ch + '</span>';
                    } else {
                        html += ch;
                    }
                }
            } else {
                html = escapeHtml(preview);
            }
            previewEl.innerHTML = html || '<span class="text-gray-600">(empty)</span>';

            // Tag info
            if (tag1Info) {
                if (result.tag1Len > 0) {
                    var t1name = RTPLUS_TYPES[result.tag1Type] ? RTPLUS_TYPES[result.tag1Type][0] : 'Type' + result.tag1Type;
                    tag1Info.textContent = t1name + ' [' + result.tag1Start + '-' + (result.tag1Start + result.tag1Len - 1) + ']';
                } else { tag1Info.textContent = '-'; }
            }
            if (tag2Info) {
                if (result.tag2Len > 0) {
                    var t2name = RTPLUS_TYPES[result.tag2Type] ? RTPLUS_TYPES[result.tag2Type][0] : 'Type' + result.tag2Type;
                    tag2Info.textContent = t2name + ' [' + result.tag2Start + '-' + (result.tag2Start + result.tag2Len - 1) + ']';
                } else { tag2Info.textContent = '-'; }
            }
        }

        function applyERTTaggingPolicies(content) {
            // Same logic as applyTaggingPolicies but uses ertTaggingPolicies
            var result = { text: content, tag1Start: -1, tag1Len: 0, tag1Type: 0,
                           tag2Start: -1, tag2Len: 0, tag2Type: 0, appliedPolicy: '' };
            var subTaggingMatches = 0; // Track how many sub-tagging policies have matched
            for (var i = 0; i < ertTaggingPolicies.length; i++) {
                var policy = ertTaggingPolicies[i];
                if (!policy.enabled) continue;
                if (policy.type === 'default') {
                    var prefix = policy.settings.prefix || '';
                    var suffix = policy.settings.suffix || '';
                    var splitPattern = policy.settings.split_pattern || ' - ';
                    result.text = prefix + content + suffix;
                    result.appliedPolicy = policy.name;
                    result.tag1Type = parseInt(policy.settings.tag1_type) || 0;
                    result.tag2Type = parseInt(policy.settings.tag2_type) || 0;
                    if (splitPattern && content.indexOf(splitPattern) !== -1) {
                        var parts = content.split(splitPattern, 2);
                        result.tag1Start = prefix.length;
                        result.tag1Len = parts[0].length;
                        result.tag2Start = prefix.length + parts[0].length + splitPattern.length;
                        result.tag2Len = parts[1].length;
                    } else {
                        result.tag1Start = prefix.length;
                        result.tag1Len = content.length;
                    }
                    continue;
                } else if (policy.type === 'sub') {
                    if (policy.settings.trigger_type && policy.settings.trigger_type !== 'none') {
                        var tpat = policy.settings.trigger_pattern || '';
                        if (!tpat) continue;
                        var tmatch = false;
                        try {
                            switch (policy.settings.trigger_type) {
                                case 'contains': tmatch = content.toLowerCase().includes(tpat.toLowerCase()); break;
                                case 'starts_with': tmatch = content.toLowerCase().startsWith(tpat.toLowerCase()); break;
                                case 'ends_with': tmatch = content.toLowerCase().endsWith(tpat.toLowerCase()); break;
                                case 'equals': tmatch = content.toLowerCase() === tpat.toLowerCase(); break;
                                case 'regex': tmatch = new RegExp(tpat, 'i').test(content); break;
                            }
                        } catch(e) {}
                        if (!tmatch) continue;
                    }
                    var matches = false;
                    var pat = policy.settings.pattern || '';
                    try {
                        switch (policy.settings.condition) {
                            case 'starts_with': matches = content.toLowerCase().startsWith(pat.toLowerCase()); break;
                            case 'ends_with': matches = content.toLowerCase().endsWith(pat.toLowerCase()); break;
                            case 'contains': matches = content.toLowerCase().includes(pat.toLowerCase()); break;
                            case 'equals': matches = content.toLowerCase() === pat.toLowerCase(); break;
                            case 'regex': matches = new RegExp(pat, 'i').test(content); break;
                        }
                    } catch(e) {}
                    if (matches) {
                        result.appliedPolicy = policy.name;
                        var tagStart = 0, tagContent = content;
                        switch (policy.settings.action) {
                            case 'tag_after':  var ai = content.indexOf(pat); if (ai !== -1) { tagStart = ai + pat.length; tagContent = content.substring(tagStart); } break;
                            case 'tag_before': var bi = content.indexOf(pat); if (bi !== -1) { tagContent = content.substring(0, bi); } break;
                            case 'tag_match':  var mi = content.indexOf(pat); if (mi !== -1) { tagStart = mi; tagContent = pat; } break;
                        }
                        if (policy.settings.strip_pattern && policy.settings.action !== 'tag_match')
                            tagContent = tagContent.replace(new RegExp(escapeRegex(pat), 'gi'), '');

                        // Apply to tag1 or tag2 depending on match count
                        if (subTaggingMatches === 0) {
                            result.tag1Type = parseInt(policy.settings.tag_type) || 0;
                            result.tag1Start = tagStart;
                            result.tag1Len = tagContent.length;
                            subTaggingMatches++;
                        } else if (subTaggingMatches === 1) {
                            result.tag2Type = parseInt(policy.settings.tag_type) || 0;
                            result.tag2Start = tagStart;
                            result.tag2Len = tagContent.length;
                            subTaggingMatches++;
                            return result; // Stop after 2 tags
                        }
                    }
                }
            }
            return result;
        }

        function addERTTaggingPolicy() {
            var policy = { id: Date.now(), name: 'New Policy', type: 'default', enabled: true,
                settings: { tag1_type: 4, tag2_type: 1, split_pattern: ' - ', prefix: '', suffix: '',
                             trigger_type: 'none', trigger_pattern: '', condition: 'starts_with',
                             pattern: '', action: 'tag_all', tag_type: 1, strip_pattern: false } };
            ertTaggingPolicies.push(policy);
            renderERTTaggingPolicies();
            editERTTaggingPolicy(policy.id);
        }

        function editERTTaggingPolicy(policyId) {
            var policy = ertTaggingPolicies.find(function(p) { return p.id === policyId; });
            if (!policy) return;
            ertCurrentEditingPolicyId = policyId;
            document.getElementById('ert_policy_name').value = policy.name;
            document.getElementById('ert_policy_type').value = policy.type;
            populateRTPlusSelect(document.getElementById('ert_policy_default_tag1'), policy.settings.tag1_type || 4);
            populateRTPlusSelect(document.getElementById('ert_policy_default_tag2'), policy.settings.tag2_type || 1);
            populateRTPlusSelect(document.getElementById('ert_policy_sub_tag_type'), policy.settings.tag_type || 1);
            document.getElementById('ert_policy_default_split').value = policy.settings.split_pattern || ' - ';
            document.getElementById('ert_policy_default_prefix').value = policy.settings.prefix || '';
            document.getElementById('ert_policy_default_suffix').value = policy.settings.suffix || '';
            document.getElementById('ert_policy_sub_trigger_type').value = policy.settings.trigger_type || 'none';
            document.getElementById('ert_policy_sub_trigger_pattern').value = policy.settings.trigger_pattern || '';
            document.getElementById('ert_policy_sub_condition').value = policy.settings.condition || 'starts_with';
            document.getElementById('ert_policy_sub_pattern').value = policy.settings.pattern || '';
            document.getElementById('ert_policy_sub_action').value = policy.settings.action || 'tag_all';
            document.getElementById('ert_policy_sub_strip_pattern').checked = policy.settings.strip_pattern || false;
            updateERTPolicyEditor();
            document.getElementById('ert_policy_editor').style.display = 'block';
            document.getElementById('ert_policy_editor_title').textContent = 'Edit Policy: ' + policy.name;
        }

        function updateERTPolicyEditor() {
            var type = document.getElementById('ert_policy_type').value;
            document.getElementById('ert_default_policy_settings').style.display = type === 'default' ? 'block' : 'none';
            document.getElementById('ert_sub_policy_settings').style.display = type === 'sub' ? 'block' : 'none';
        }

        function saveERTPolicyEditor() {
            if (!ertCurrentEditingPolicyId) return;
            var policy = ertTaggingPolicies.find(function(p) { return p.id === ertCurrentEditingPolicyId; });
            if (!policy) return;
            policy.name = document.getElementById('ert_policy_name').value;
            policy.type = document.getElementById('ert_policy_type').value;
            if (policy.type === 'default') {
                policy.settings.tag1_type = parseInt(document.getElementById('ert_policy_default_tag1').value);
                policy.settings.tag2_type = parseInt(document.getElementById('ert_policy_default_tag2').value);
                policy.settings.split_pattern = document.getElementById('ert_policy_default_split').value;
                policy.settings.prefix = document.getElementById('ert_policy_default_prefix').value;
                policy.settings.suffix = document.getElementById('ert_policy_default_suffix').value;
            } else {
                policy.settings.trigger_type = document.getElementById('ert_policy_sub_trigger_type').value;
                policy.settings.trigger_pattern = document.getElementById('ert_policy_sub_trigger_pattern').value;
                policy.settings.condition = document.getElementById('ert_policy_sub_condition').value;
                policy.settings.pattern = document.getElementById('ert_policy_sub_pattern').value;
                policy.settings.action = document.getElementById('ert_policy_sub_action').value;
                policy.settings.tag_type = parseInt(document.getElementById('ert_policy_sub_tag_type').value);
                policy.settings.strip_pattern = document.getElementById('ert_policy_sub_strip_pattern').checked;
            }
            closeERTPolicyEditor();
            renderERTTaggingPolicies();
            updateERTRTPlusPreview();
        }

        function closeERTPolicyEditor() {
            ertCurrentEditingPolicyId = null;
            document.getElementById('ert_policy_editor').style.display = 'none';
        }

        function deleteERTTaggingPolicy(policyId) {
            if (!confirm('Delete this tagging policy?')) return;
            ertTaggingPolicies = ertTaggingPolicies.filter(function(p) { return p.id !== policyId; });
            renderERTTaggingPolicies();
            updateERTRTPlusPreview();
        }

        function toggleERTTaggingPolicy(policyId) {
            var p = ertTaggingPolicies.find(function(p) { return p.id === policyId; });
            if (p) { p.enabled = !p.enabled; renderERTTaggingPolicies(); updateERTRTPlusPreview(); }
        }

        function moveERTTaggingPolicy(policyId, direction) {
            var idx = ertTaggingPolicies.findIndex(function(p) { return p.id === policyId; });
            if (idx === -1) return;
            if (direction === 'up' && idx > 0) {
                var t = ertTaggingPolicies[idx]; ertTaggingPolicies[idx] = ertTaggingPolicies[idx-1]; ertTaggingPolicies[idx-1] = t;
            } else if (direction === 'down' && idx < ertTaggingPolicies.length - 1) {
                var t = ertTaggingPolicies[idx]; ertTaggingPolicies[idx] = ertTaggingPolicies[idx+1]; ertTaggingPolicies[idx+1] = t;
            }
            renderERTTaggingPolicies();
            updateERTRTPlusPreview();
        }

        function renderERTTaggingPolicies() {
            var container = document.getElementById('ert_tagging_policies_list');
            if (!container) return;
            container.innerHTML = '';
            if (ertTaggingPolicies.length === 0) {
                container.innerHTML = '<div class="text-center text-gray-500 text-xs py-4 border border-gray-700 rounded">No policies defined. Click "+ Add Policy" to create one.</div>';
                return;
            }
            ertTaggingPolicies.forEach(function(policy, index) {
                var typeIcon = policy.type === 'default' ? '📍' : '⚡';
                var typeColor = policy.type === 'default' ? 'green' : 'purple';
                var typeText = policy.type === 'default' ? 'Default' : 'Sub-tagging';
                var description = '';
                if (policy.type === 'default') {
                    var t1n = RTPLUS_TYPES[policy.settings.tag1_type] ? RTPLUS_TYPES[policy.settings.tag1_type][0] : 'Unknown';
                    var t2n = RTPLUS_TYPES[policy.settings.tag2_type] ? RTPLUS_TYPES[policy.settings.tag2_type][0] : 'Unknown';
                    description = t1n + ' + ' + t2n + ' • Split: "' + (policy.settings.split_pattern || ' - ') + '"';
                } else {
                    var ttn = RTPLUS_TYPES[policy.settings.tag_type] ? RTPLUS_TYPES[policy.settings.tag_type][0] : 'Unknown';
                    description = ttn + ' • ' + (policy.settings.condition || '').replace('_', ' ') + ': "' + (policy.settings.pattern || '') + '"';
                }
                var html = '<div class="bg-[#1a1a1a] border border-' + typeColor + '-800/50 rounded p-3">';
                html += '<div class="flex items-center justify-between mb-2">';
                html += '<div class="flex items-center gap-3"><label class="flex items-center gap-2 cursor-pointer">';
                html += '<input type="checkbox" ' + (policy.enabled ? 'checked' : '') + ' onchange="toggleERTTaggingPolicy(' + policy.id + ')" class="accent-' + typeColor + '-600">';
                html += '<div class="flex items-center gap-2"><span class="text-lg">' + typeIcon + '</span>';
                html += '<div><div class="text-sm font-bold text-white">' + escapeHtml(policy.name) + '</div>';
                html += '<div class="text-xs text-' + typeColor + '-400">' + typeText + '</div></div></div></label></div>';
                html += '<div class="flex gap-1">';
                html += '<button onclick="moveERTTaggingPolicy(' + policy.id + ', \'up\')" ' + (index === 0 ? 'disabled' : '') + ' class="w-6 h-6 text-xs bg-gray-700 hover:bg-gray-600 disabled:bg-gray-800 disabled:text-gray-600 rounded">↑</button>';
                html += '<button onclick="moveERTTaggingPolicy(' + policy.id + ', \'down\')" ' + (index === ertTaggingPolicies.length - 1 ? 'disabled' : '') + ' class="w-6 h-6 text-xs bg-gray-700 hover:bg-gray-600 disabled:bg-gray-800 disabled:text-gray-600 rounded">↓</button>';
                html += '<button onclick="editERTTaggingPolicy(' + policy.id + ')" class="w-6 h-6 text-xs bg-blue-800 hover:bg-blue-700 rounded text-white">✏</button>';
                html += '<button onclick="deleteERTTaggingPolicy(' + policy.id + ')" class="w-6 h-6 text-xs bg-red-800 hover:bg-red-700 rounded text-white">×</button>';
                html += '</div></div><div class="text-xs text-gray-400">' + description + '</div></div>';
                container.innerHTML += html;
            });
        }

        function saveERTMessage() {
            var id = document.getElementById('ert_msg_edit_id').value;
            var msg = ertMessages.find(function(m) { return m.id === id; });
            
            if (!msg && pendingNewERTMessage && pendingNewERTMessage.id === id) {
                msg = pendingNewERTMessage;
                ertMessages.push(msg);
                pendingNewERTMessage = null;
            }
            
            if (!msg) return;
            
            // Save basic fields
            msg.cycles = parseInt(document.getElementById('ert_msg_cycles').value) || 2;
            msg.enabled = document.getElementById('ert_msg_enabled').checked;
            msg.source_type = document.querySelector('input[name="ert_msg_source"]:checked').value;
            
            // Save content based on source type
            if (msg.source_type === 'manual') {
                var textElement = document.getElementById('ert_msg_text');
                if (textElement) {
                    msg.content = textElement.value || '';
                    console.log('Saving manual eRT content:', msg.content);
                } else {
                    console.error('ert_msg_text element not found!');
                    msg.content = '';
                }
            } else {
                var contentElement = document.getElementById('ert_msg_content');
                if (contentElement) {
                    msg.content = contentElement.value || '';
                    console.log('Saving file/URL eRT content:', msg.content);
                } else {
                    console.error('ert_msg_content element not found!');
                    msg.content = '';
                }
            }
            
            // Save RT+ configuration
            msg.rt_plus_enabled = document.getElementById('ert_msg_rtplus_enabled').checked;
            msg.tagging_policies = msg.rt_plus_enabled ? JSON.stringify(ertTaggingPolicies) : '[]';
            // Store sample text for reload
            var sampleEl = document.getElementById('ert_msg_sample_text');
            msg.sample_text = sampleEl ? (sampleEl.value || '') : '';

            // Save text trim flags
            var etpEl = document.getElementById('ert_msg_trim_parens'); msg.trim_parens = etpEl ? etpEl.checked : false;
            etpEl = document.getElementById('ert_msg_trim_brackets'); msg.trim_brackets = etpEl ? etpEl.checked : false;
            etpEl = document.getElementById('ert_msg_trim_semicolon'); msg.trim_at_semicolon = etpEl ? etpEl.checked : false;
            etpEl = document.getElementById('ert_msg_trim_maxlen'); msg.trim_max_len = etpEl ? etpEl.checked : false;
            
            closeERTMsgModal();
            renderERTMessages();
            syncERTMessages();
        }

        function closeERTMsgModal() {
            document.getElementById('ert_msg_modal').style.display = 'none';
            pendingNewERTMessage = null;
        }

        function toggleERTMessage(id) {
            var msg = ertMessages.find(function(m) { return m.id === id; });
            if (msg) {
                msg.enabled = !msg.enabled;
                renderERTMessages();
                syncERTMessages();
            }
        }

        function deleteERTMessage(id) {
            if (!confirm('Delete this eRT message?')) return;
            ertMessages = ertMessages.filter(function(m) { return m.id !== id; });
            renderERTMessages();
            syncERTMessages();
        }

        function syncERTMessages() {
            socket.emit('update', { ert_messages: JSON.stringify(ertMessages) });
        }

        function updateERTSourceUI() {
            var src = document.getElementById('ert_source');
            var isRT = src && src.value === 'rt';
            var addBtn = document.getElementById('ert_add_msg_btn');
            var msgSection = document.getElementById('ert_msg_section');
            var rtNote = document.getElementById('ert_source_rt_note');
            if (addBtn) addBtn.style.display = isRT ? 'none' : '';
            if (msgSection) msgSection.style.display = isRT ? 'none' : '';
            if (rtNote) rtNote.style.display = isRT ? '' : 'none';
        }

        // Initialize eRT UI on page load
        document.addEventListener('DOMContentLoaded', function() {
            renderERTMessages();
            updateERTSourceUI();
            
            // Add event listeners for eRT RT+ preview updates
            var ertTagElements = [
                'ert_msg_tag1_type', 'ert_msg_tag2_type', 
                'ert_msg_split_pattern', 'ert_msg_prefix', 'ert_msg_suffix'
            ];
            
            ertTagElements.forEach(function(id) {
                var element = document.getElementById(id);
                if (element) {
                    element.addEventListener('change', updateERTRTPlusPreview);
                    element.addEventListener('input', updateERTRTPlusPreview);
                }
            });
        });

    </script>
</body>
</html>
"""

if __name__ == '__main__':
    print("[STARTUP] RDS Encoder application starting...", flush=True)
    print(f"[STARTUP] Starting threads and web server on port {http_port}", flush=True)
    socketio.run(app, host='0.0.0.0', port=http_port, debug=False)
