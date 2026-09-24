"""Everything the UI is allowed to ask the mouse for.

Each call opens its own handle: the firmware gets unreliable when a handle is
reused after reading battery messages. Calls are made one at a time from the
app's worker thread.

Values are range-checked here because malformed packets can be written to the
mouse permanently, and there is no firmware recovery.
"""
import os
import select
import time

import device
import store
from keymap import ACTION_LABELS, KEYBOARD_ACTION, MACRO_ACTION

LIGHT_MODES = range(0, 7)
BRIGHTNESS = range(1, 9)
LED_SPEEDS = range(1, 6)
STAGES = range(1, 9)
POLLING_RATES = (125, 250, 500, 1000)
ASLEEP_AFTER = 6  # the mouse reports every ~2s while awake
LEFT_CLICK = 0x02
KNOWN_ACTIONS = set(ACTION_LABELS)


class ValidationError(Exception):
	pass


def _int(value, low, high, name):
	try:
		number = int(round(float(value)))
	except (TypeError, ValueError):
		raise ValidationError(f'{name} must be a number') from None
	if not low <= number <= high:
		raise ValidationError(f'{name} must be between {low} and {high}')
	return number


def _rgb(value, name):
	try:
		r, g, b = value
	except (TypeError, ValueError):
		raise ValidationError(f'{name} must be a red, green, blue triple') from None
	return tuple(_int(c, 0, 255, name) for c in (r, g, b))


def _slot(value, name):
	try:
		action, modifiers, usage = value
	except (TypeError, ValueError):
		raise ValidationError(f'{name} must be an action, modifiers, key triple') from None
	action = _int(action, 1, 255, f'{name} action')
	if action not in KNOWN_ACTIONS:
		raise ValidationError(f'{name} is not an action this mouse knows (0x{action:02x})')
	return [action, _int(modifiers, 0, 15, f'{name} modifiers'), _int(usage, 0, 255, f'{name} key')]


def _with_device(work):
	with device.Device() as d:
		return work(d)


# --- reads -------------------------------------------------------------------

def on_cable():
	"""True when the mouse is connected to this PC by its USB cable."""
	path = device.find_device()
	return bool(path) and device.is_wired(path)


def get_performance():
	def work(d):
		dpi = d.get_dpi()
		time.sleep(device.SETTLE)
		return dpi, d.get_polling_rate()

	return _with_device(work)


def get_lighting():
	return _with_device(lambda d: d.get_lighting())


def get_buttons():
	return _with_device(lambda d: d.get_buttons())


def watch_battery(on_reading, on_missing, on_asleep, stop):
	"""Follows the battery messages the mouse sends every ~2s, until `stop` is set.

	Runs on its own thread with its own read-only handle, apart from the worker's:
	it never writes, and it sleeps in select() between messages, so it costs
	nothing while waiting. on_reading(percent, status, estimate, settling, wired) is
	called only when something shown would change; on_missing(message) when the
	receiver is gone; on_asleep() when the mouse goes quiet for ASLEEP_AFTER
	seconds, the same test the vendor software uses. All are called from this thread.
	"""
	while not stop.is_set():
		path = device.find_device()
		if not path:
			on_missing('No Attack Shark X11 found. Is the receiver plugged in?')
			stop.wait(5)
			continue
		try:
			fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
		except OSError as e:
			on_missing(f'Could not open {path}: {e.strerror}. The udev rule may be missing; see the README.')
			stop.wait(5)
			continue
		wired = device.is_wired(path)  # on the cable to this PC, not a dock or charger
		follower = _BatteryFollower(wired, on_reading, on_asleep)
		try:
			while not stop.is_set():
				if select.select([fd], [], [], ASLEEP_AFTER)[0]:
					follower.report(os.read(fd, 64))
				else:
					follower.quiet()
		except OSError:
			on_missing('The mouse was disconnected.')
		finally:
			os.close(fd)
		stop.wait(2)


class _BatteryFollower:
	"""What watch_battery remembers between messages, and what it does with each."""

	def __init__(self, wired, on_reading, on_asleep):
		self.wired, self.on_reading, self.on_asleep = wired, on_reading, on_asleep
		self.shown = None
		self.first = True
		self.asleep = False
		self.last_record = 0.0
		self.last_heard = time.monotonic()

	def quiet(self):
		if not self.asleep and time.monotonic() - self.last_heard >= ASLEEP_AFTER:
			self.asleep, self.shown, self.first = True, None, True
			self.on_asleep()

	def _is_placeholder(self, reading):
		# The first report after connecting or waking can be a placeholder 100%.
		if not self.first:
			return False
		self.first = False
		return reading == (100, device.BATTERY_DISCHARGING)

	def report(self, data):
		self.last_heard = time.monotonic()
		self.asleep = False
		reading = device.parse_battery(data)
		if not reading or self._is_placeholder(reading):
			return
		now = time.monotonic()
		if self.shown and reading == self.shown[:2] and now - self.last_record < store.RECORD_EVERY:
			return
		self.last_record = now
		percent, status = reading
		estimate, settling = store.record_battery(percent, status)
		state = (percent, status, estimate, settling, self.wired)
		if state != self.shown:
			self.shown = state
			self.on_reading(*state)


# --- writes ------------------------------------------------------------------

def set_performance(values, colors, current_stage, active_stages, angle_snap, ripple_control, rate=None):
	if len(values) != 8 or len(colors) != 8:
		raise ValidationError('Expected 8 DPI stages')
	checked = [0 if not v else _int(v, 50, 26000, f'Stage {i + 1}') for i, v in enumerate(values)]
	colors = [_rgb(c, f'Stage {i + 1} colour') for i, c in enumerate(colors)]
	stage = _int(current_stage, 1, 8, 'Active stage')
	mask = _int(active_stages, 1, 255, 'Enabled stages')
	if not mask & (1 << (stage - 1)):
		raise ValidationError('The active stage must be one of the enabled stages')
	if checked[stage - 1] == 0:
		raise ValidationError('The active stage cannot be left unset')
	if rate is not None and rate not in POLLING_RATES:
		raise ValidationError(f'Polling rate must be one of {POLLING_RATES}')

	settings = {
		'values': checked, 'colors': colors, 'current_stage': stage, 'active_stages': mask,
		'angle_snap': bool(angle_snap), 'ripple_control': bool(ripple_control),
	}

	def work(d):
		d.set_dpi(settings)
		if rate is not None:
			d.set_polling_rate(rate)
			time.sleep(device.SETTLE)
		dpi = d.get_dpi()
		time.sleep(device.SETTLE)
		return dpi, d.get_polling_rate()

	return _with_device(work)


def set_lighting(**changes):
	"""Lighting, sleep timers and debounce share one packet, so unset fields keep
	whatever the mouse currently has rather than reverting to defaults."""
	allowed = {'mode', 'rgb', 'speed', 'brightness', 'sleep_time', 'deep_sleep_time', 'key_response'}
	unknown = set(changes) - allowed
	if unknown:
		raise ValidationError(f'Unknown lighting setting: {", ".join(sorted(unknown))}')
	if 'mode' in changes:
		changes['mode'] = _int(changes['mode'], 0, 6, 'Light mode')
		if changes['mode'] not in LIGHT_MODES:
			raise ValidationError('Unknown light mode')
	if 'rgb' in changes:
		changes['rgb'] = _rgb(changes['rgb'], 'Colour')
	for key, span, name in (('speed', LED_SPEEDS, 'Animation speed'),
	                        ('brightness', BRIGHTNESS, 'Brightness')):
		if key in changes:
			changes[key] = _int(changes[key], span[0], span[-1], name)
	if 'sleep_time' in changes:
		halves = _int(float(changes['sleep_time']) * 2, 1, 60, 'Sleep time')
		changes['sleep_time'] = halves / 2
	if 'deep_sleep_time' in changes:
		changes['deep_sleep_time'] = _int(changes['deep_sleep_time'], 1, 60, 'Deep sleep time')
	if 'key_response' in changes:
		ms = _int(changes['key_response'], 4, 50, 'Click debounce')
		if ms % 2:
			raise ValidationError('Click debounce must be an even number of milliseconds')
		changes['key_response'] = ms

	def work(d):
		current = d.get_lighting()
		time.sleep(device.SETTLE)
		d.set_lighting({**current, **changes})
		return d.get_lighting()

	return _with_device(work)


def set_buttons(changes):
	wanted = {_int(slot, 1, 18, 'Button'): _slot(value, f'Button {slot}')
	          for slot, value in changes.items()}
	def work(d):
		slots = d.get_buttons()
		slots.update(wanted)
		# Checked against the whole mapping, not just what the caller sent: an
		# imported profile could otherwise leave the mouse unable to click, and
		# undoing that needs a working click.
		if not any(slot[0] == LEFT_CLICK for slot in slots.values()):
			raise ValidationError('At least one button must stay set to Left click')
		time.sleep(device.SETTLE)
		return d.set_buttons(slots)

	return _with_device(work)


def set_macro(button, events, mode=0, repeat=1):
	if button not in device.MACRO_BUTTONS:
		raise ValidationError(f'Only buttons {", ".join(map(str, device.MACRO_BUTTONS))} can hold a macro')
	if not events:
		raise ValidationError('A macro needs at least one step')
	checked = [{
		'code': _int(e.get('code'), 1, 255, 'Macro step'),
		'release': bool(e.get('release')),
		'delayMs': _int(e.get('delayMs', 10), 10, 50000, 'Macro delay'),
	} for e in events]
	mode = _int(mode, 0, 2, 'Play mode')
	repeat = _int(repeat, 1, 255, 'Repeat count')
	try:
		device.encode_macro_events(checked)   # raises if it will not fit
	except device.DeviceError as e:
		raise ValidationError(str(e)) from None

	def work(d):
		return d.set_macro(button, checked, mode, repeat)

	return _with_device(work)


def reset():
	return _with_device(lambda d: d.reset())


def describe_battery_status(status):
	return {
		device.BATTERY_CHARGING: 'Charging',
		device.BATTERY_FULL: 'Fully charged',
		device.BATTERY_DISCHARGING: 'On battery',
	}.get(status, 'Connected')


__all__ = [
	'ValidationError', 'on_cable', 'watch_battery', 'describe_battery_status', 'get_buttons', 'get_lighting',
	'get_performance', 'reset', 'set_buttons', 'set_lighting', 'set_macro', 'set_performance',
	'KEYBOARD_ACTION', 'MACRO_ACTION',
]
