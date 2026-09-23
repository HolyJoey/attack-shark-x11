"""On-disk state: saved profiles, and the macros the mouse can't read back."""
import json
import os
import time
import re
import shutil

CONFIG_DIR = os.path.join(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')), 'attack-shark-x11')
PROFILE_DIR = os.path.join(CONFIG_DIR, 'profiles')
MACRO_FILE = os.path.join(CONFIG_DIR, 'macros.json')
POINTER_FILE = os.path.join(CONFIG_DIR, 'pointer.json')
BATTERY_FILE = os.path.join(CONFIG_DIR, 'battery.json')


def _ensure():
	os.makedirs(PROFILE_DIR, exist_ok=True)


def _read_json(path, fallback):
	try:
		with open(path) as f:
			return json.load(f)
	except (OSError, json.JSONDecodeError):
		return fallback


def _write_json(path, data):
	_ensure()
	tmp = path + '.tmp'
	with open(tmp, 'w') as f:
		json.dump(data, f, indent=2)
	os.replace(tmp, path)


def safe_name(name):
	cleaned = re.sub(r'[^\w -]', '', name).strip()
	return cleaned or 'profile'


def profile_path(name):
	return os.path.join(PROFILE_DIR, safe_name(name) + '.json')


def profile_exists(name):
	return os.path.exists(profile_path(name))


def list_profiles():
	_ensure()
	return sorted(f[:-5] for f in os.listdir(PROFILE_DIR) if f.endswith('.json'))


def save_profile(name, data):
	_write_json(profile_path(name), data)


def load_profile(name):
	return _read_json(profile_path(name), None)


def delete_profile(name):
	try:
		os.remove(profile_path(name))
	except OSError:
		pass


def rename_profile(old, new):
	os.replace(profile_path(old), profile_path(new))


def export_profile(name, dest):
	shutil.copyfile(profile_path(name), dest)


def import_profile(src):
	data = _read_json(src, None)
	if not isinstance(data, dict):
		raise ValueError('That file is not a profile')
	name = safe_name(data.get('name') or os.path.basename(src).removesuffix('.json'))
	save_profile(name, {**data, 'name': name})
	return name


# Macros: the device has no read-back for them, so the app keeps its own copy.
def load_macros():
	return {str(k): v for k, v in _read_json(MACRO_FILE, {}).items()}


def save_macro(button, macro):
	macros = load_macros()
	macros[str(button)] = macro
	_write_json(MACRO_FILE, macros)


def delete_macro(button):
	macros = load_macros()
	if macros.pop(str(button), None) is not None:
		_write_json(MACRO_FILE, macros)


def load_pointer():
	return _read_json(POINTER_FILE, {})


def save_pointer(settings):
	_write_json(POINTER_FILE, settings)


# --- battery history ---------------------------------------------------------
# The mouse reports a percentage but no runtime estimate, so we keep readings
# over time and work out how fast it is moving. The percentage comes from the
# battery voltage, which sits high for a while after any time on the dock, so
# readings just after undocking are left out of the drain rate.
#
# The last good charge and drain rates are remembered, so an estimate can be
# shown straight away instead of after a fresh 20 minutes of readings.

MAX_SAMPLES = 400
RECORD_EVERY = 60        # the mouse reports every ~2s; one stored sample a minute is plenty
SETTLE_TIME = 15 * 60    # voltage recovery after charging
RECHARGE_JUMP = 5        # a rise this big on battery means it was charged in between
_MIN_SPAN = 20 * 60      # live readings needed before trusting a fresh rate
_WINDOW = 6 * 3600
_MAX_SPAN = 14 * 24 * 3600
DISCHARGING, FULL, CHARGING = 1, 2, 3


def load_battery():
	data = _read_json(BATTERY_FILE, {})
	if isinstance(data, list):  # older files were just the sample list
		data = {'samples': data}
	if not isinstance(data, dict):
		data = {}
	data.setdefault('samples', [])
	data.setdefault('rates', {})
	data.setdefault('settle_until', 0)
	return data


def record_battery(percent, status, now=None):
	"""Note a reading. Returns (estimate, settling): a string like '6h 20m left'
	or None, and whether the reading is still inflated from charging."""
	now = time.time() if now is None else now
	data = load_battery()
	samples = [s for s in data['samples'] if now - s.get('t', 0) < _MAX_SPAN]
	previous = samples[-1] if samples else None
	# Plugged in or unplugged, or clearly charged while the app wasn't watching:
	# the old trend no longer applies.
	recharged = (previous and status == DISCHARGING == previous.get('status')
	             and percent >= previous.get('percent', 0) + RECHARGE_JUMP)
	if previous and (previous.get('status') != status or recharged):
		samples = []
		if status == DISCHARGING:
			data['settle_until'] = now + SETTLE_TIME
	if not previous or previous.get('status') != status or now - previous['t'] >= RECORD_EVERY:
		samples.append({'t': now, 'percent': percent, 'status': status})
		data['samples'] = samples[-MAX_SAMPLES:]
		rate = measured_rate(data['samples'], status, data['settle_until'])
		if rate:
			data['rates'][str(status)] = rate
		_write_json(BATTERY_FILE, data)
	settling = status == DISCHARGING and now < data['settle_until']
	rate = data['rates'].get(str(status))
	return estimate_remaining(percent, status, rate), settling


def measured_rate(samples, status, settle_until=0):
	"""Percent per second from recent readings, or None if there is too little to go on."""
	if status not in (DISCHARGING, CHARGING) or not samples:
		return None
	last = samples[-1]['t']
	# Recent readings only: a mouse that sat at 100% for days would otherwise
	# average out to a rate so slow that no estimate is ever shown.
	recent = [s for s in samples if last - s['t'] <= _WINDOW and s['t'] >= settle_until]
	if len(recent) < 2 or recent[-1]['t'] - recent[0]['t'] < _MIN_SPAN:
		return None
	# Least-squares slope, so one reading that jitters by a percent doesn't swing it.
	n = len(recent)
	mean_t = sum(s['t'] for s in recent) / n
	mean_p = sum(s['percent'] for s in recent) / n
	num = sum((s['t'] - mean_t) * (s['percent'] - mean_p) for s in recent)
	den = sum((s['t'] - mean_t) ** 2 for s in recent)
	slope = num / den if den else 0
	moved = slope if status == CHARGING else -slope
	return moved if moved > 0 else None


def estimate_remaining(percent, status, rate):
	if not rate or status not in (DISCHARGING, CHARGING):
		return None
	remaining = (100 - percent) if status == CHARGING else percent
	seconds = remaining / rate
	if seconds > _MAX_SPAN:
		return None
	hours, minutes = int(seconds // 3600), int((seconds % 3600) // 60)
	shape = 'About {} to full' if status == CHARGING else 'About {} left'
	return shape.format(f'{hours}h {minutes}m' if hours else f'{minutes}m')
