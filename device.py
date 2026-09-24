"""Talks to the Attack Shark X11 over hidraw.

Ported from the TypeScript driver by HarukaYamamoto0 (MIT), with fixes found by
testing against real hardware:
  * the lighting packet's checksum is recalculated on every write
  * the light mode byte is shifted back down when read
  * slots are read at the same offsets they are written to
  * macro packets follow the captured traffic, not the protocol notes

Settings live in "feature reports". Reading one is a two-step dance: ask report
0xA0 to unlock a report id, then read that id.
"""
import ctypes
import fcntl
import glob
import os
import select
import time

from dpi_table import DPI_3311

# The table's last 20 bytes repeat entries 100-119, so anything at index 200 or
# beyond is indistinguishable from a lower step: writing DPI 20300 that way came
# back as 10300. Only the unique part is safe to encode with.
USABLE_STEPS = 200

VENDOR_ID = 0x1D57
PRODUCT_ID = 0xFA60          # 2.4 GHz receiver
PRODUCT_ID_WIRED = 0xFA55
CONFIG_INTERFACE = 2         # the vendor interface, where settings live

REPORT_DPI = 0x04
REPORT_LIGHTING = 0x05
REPORT_POLLING = 0x06
REPORT_BUTTONS = 0x08
REPORT_MACRO = 0x09
REPORT_PROFILE = 0x0C
REPORT_UNLOCK = 0xA0

LEN_DPI = 0x38
LEN_LIGHTING = 0x0F
LEN_POLLING = 0x09
LEN_BUTTONS = 0x3B

# Pause between packets: the firmware drops commands sent back to back.
SETTLE = 0.25

# Over the USB cable the mouse declares shorter DPI, lighting and profile reports.
# The vendor software builds the same packets as for the receiver (header byte
# included) and lets the driver cut them to these sizes; the checksums sit inside
# the kept part. Everything else is the same length on both connections.
WIRED_LENGTHS = {0x04: 0x34, 0x05: 0x0D, 0x0C: 0x06}
# Over the cable the mouse stalls every other feature write; the vendor software
# retries up to five times, 200 ms apart. A stalled write is not applied.
WIRED_ATTEMPTS = 5
WIRED_RETRY_GAP = 0.2

POLLING_RATES = {0x08: 125, 0x04: 250, 0x02: 500, 0x01: 1000}
RATE_CODES = {hz: code for code, hz in POLLING_RATES.items()}
SLEEP_STEPS = [round(0.5 * i, 1) for i in range(1, 61)]

MACRO_BUTTONS = (1, 2, 3, 7, 8)
MACRO_ACTION = 0x12
MAX_MACRO_EVENTS = 47

BATTERY_DISCHARGING = 0x01
BATTERY_FULL = 0x02
BATTERY_CHARGING = 0x03
MESSAGE_BATTERY = (0x40, 0x41)


class DeviceError(Exception):
	pass


class DeviceNotFound(DeviceError):
	pass


class WiredUnsupported(DeviceError):
	pass


# --- hidraw plumbing ---------------------------------------------------------

_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(direction, type_char, nr, size):
	return (direction << 30) | (size << 16) | (ord(type_char) << 8) | nr


def _hidiocgfeature(size):
	return _ioc(_IOC_WRITE | _IOC_READ, 'H', 0x07, size)


def _hidiocsfeature(size):
	return _ioc(_IOC_WRITE | _IOC_READ, 'H', 0x06, size)


def find_device():
	"""Path of the mouse's config interface, or None. The cable wins over the
	receiver when both are present, since the mouse then talks over the cable."""
	found = {}
	for path in sorted(glob.glob('/sys/class/hidraw/hidraw*')):
		node = '/dev/' + os.path.basename(path)
		try:
			uevent = open(os.path.join(path, 'device/uevent')).read()
		except OSError:
			continue
		for product_id in (PRODUCT_ID, PRODUCT_ID_WIRED):
			if f'HID_ID=0003:{VENDOR_ID:08X}:{product_id:08X}' not in uevent.upper():
				continue
			# .../1-10:1.2/0003:1D57:FA60.0004 -> interface 2
			real = os.path.realpath(os.path.join(path, 'device'))
			interface = real.split('/')[-2].rsplit('.', 1)[-1]
			if interface == str(CONFIG_INTERFACE):
				found.setdefault(product_id, node)
	return found.get(PRODUCT_ID_WIRED) or found.get(PRODUCT_ID)


def is_wired(path):
	"""True when the node belongs to the mouse on its USB cable rather than the receiver."""
	try:
		uevent = open(f'/sys/class/hidraw/{os.path.basename(path)}/device/uevent').read().upper()
	except OSError:
		return False
	return f'HID_ID=0003:{VENDOR_ID:08X}:{PRODUCT_ID_WIRED:08X}' in uevent


def parse_battery(message):
	"""(percent, status) from a battery message the mouse sends every ~2s, else None."""
	# Same bounds as the vendor software, which ignores 0 as well as anything over 100.
	if len(message) == 5 and message[2] in MESSAGE_BATTERY and 1 <= message[4] <= 100:
		return message[4], message[3]
	return None


class Device:
	"""One open handle to the mouse. Not thread-safe; the app keeps it on one worker thread."""

	def __init__(self, path=None):
		self.path = path or find_device()
		if not self.path:
			raise DeviceNotFound('No Attack Shark X11 found. Is the receiver plugged in?')
		self.wired = is_wired(self.path)
		try:
			self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
		except PermissionError as e:
			raise DeviceError(
				f'No permission to use {self.path}. The udev rule may be missing; see the README.'
			) from e
		except OSError as e:
			raise DeviceError(f'Could not open {self.path}: {e}') from e

	def close(self):
		try:
			os.close(self.fd)
		except OSError:
			pass

	def __enter__(self):
		return self

	def __exit__(self, *_exc):
		self.close()

	# --- raw reports ---
	def send_feature(self, data: bytes):
		data = bytes(data)
		attempts = 1
		if self.wired:
			data = data[:WIRED_LENGTHS.get(data[0], len(data))]
			attempts = WIRED_ATTEMPTS
		buf = ctypes.create_string_buffer(data, len(data))
		for attempt in range(attempts):
			try:
				fcntl.ioctl(self.fd, _hidiocsfeature(len(data)), buf, True)
				return
			except BrokenPipeError as e:
				if attempt + 1 == attempts:
					raise DeviceError(f'The mouse rejected a write ({e.strerror}). Is it asleep?') from e
				time.sleep(WIRED_RETRY_GAP)
			except OSError as e:
				raise DeviceError(f'The mouse rejected a write ({e.strerror}). Is it asleep?') from e

	def get_feature(self, report_id: int, length: int) -> bytes:
		buf = ctypes.create_string_buffer(length)
		buf[0] = bytes([report_id])
		try:
			size = fcntl.ioctl(self.fd, _hidiocgfeature(length), buf, True)
		except OSError as e:
			raise DeviceError(f'The mouse did not answer a read ({e.strerror}). Is it asleep?') from e
		return bytes(buf.raw[:size]) if size > 0 else bytes(buf.raw)

	def read_settings(self, report_id: int, length: int) -> bytes:
		"""Unlock a report id via report 0xA0, then read it. Over the cable the
		shorter report is padded back out, so parsers see one layout."""
		wire_length = WIRED_LENGTHS.get(report_id, length) if self.wired else length
		self.send_feature(bytes([REPORT_UNLOCK, report_id, wire_length, 0x00, 0x01, 0x00, 0x00, 0x00]))
		time.sleep(SETTLE)
		status = self.get_feature(REPORT_UNLOCK, 8)
		if len(status) > 1 and status[1] != 0x01:
			raise DeviceError(f'The mouse would not open report 0x{report_id:02x} for reading')
		data = self.get_feature(report_id, wire_length)
		if self.wired and len(data) < length:
			data += bytes(length - len(data))
		return data

	def drain_input(self, timeout=0.0):
		"""Input reports the mouse sends on its own (battery, DPI changes)."""
		messages = []
		deadline = time.monotonic() + timeout
		while True:
			remaining = max(0.0, deadline - time.monotonic())
			if not select.select([self.fd], [], [], remaining)[0]:
				break
			try:
				data = os.read(self.fd, 64)
			except BlockingIOError:
				break
			except OSError:
				break
			if data:
				messages.append(data)
			if time.monotonic() >= deadline:
				break
		return messages

	# --- battery ---
	def battery(self, timeout=8.0):
		"""Waits for the mouse's own battery message. Returns (percent, status) or (None, None)."""
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			for message in self.drain_input(timeout=min(1.0, deadline - time.monotonic())):
				reading = parse_battery(message)
				if reading:
					return reading
		return None, None

	# --- DPI ---
	def get_dpi(self):
		return parse_dpi(self.read_settings(REPORT_DPI, LEN_DPI))

	def set_dpi(self, settings):
		self.send_feature(build_dpi(settings))
		time.sleep(SETTLE)

	# --- lighting, sleep timers and debounce (one packet) ---
	def get_lighting(self):
		return parse_lighting(self.read_settings(REPORT_LIGHTING, LEN_LIGHTING))

	def set_lighting(self, settings):
		self.send_feature(build_lighting(settings))
		time.sleep(SETTLE)

	# --- polling rate ---
	def get_polling_rate(self):
		data = self.read_settings(REPORT_POLLING, LEN_POLLING)
		return POLLING_RATES.get(data[3])

	def set_polling_rate(self, hz):
		code = RATE_CODES.get(int(hz))
		if code is None:
			raise DeviceError(f'Unsupported polling rate: {hz}')
		self.send_feature(bytes([REPORT_POLLING, LEN_POLLING, 0x01, code, 0xFF - code, 0, 0, 0, 0]))
		time.sleep(SETTLE)

	# --- buttons ---
	def get_buttons(self):
		return parse_buttons(self.read_settings(REPORT_BUTTONS, LEN_BUTTONS))

	def set_buttons(self, slots):
		self.send_feature(build_buttons(slots))
		time.sleep(SETTLE)
		return self.get_buttons()

	def set_macro(self, button, events, mode=0, repeat=1):
		if self.wired:
			# The vendor software repacks macros into a different layout over the
			# cable; that format isn't implemented here.
			raise WiredUnsupported('Macros can only be recorded over the 2.4 GHz receiver.')
		if button not in MACRO_BUTTONS:
			raise DeviceError(f'Only buttons {MACRO_BUTTONS} can hold a macro')
		# Encode first: a macro that cannot fit must not leave the button
		# pointing at a macro that was never written.
		packets = build_macro_packets(button, events, mode, repeat)
		slots = self.get_buttons()
		slots[button] = [MACRO_ACTION, 0x00, button]
		self.send_feature(build_buttons(slots))
		time.sleep(SETTLE)
		for packet in packets:
			self.send_feature(packet)
			time.sleep(SETTLE)
		return self.get_buttons()

	# --- factory reset ---
	def reset(self):
		self.send_feature(bytes([REPORT_PROFILE, 0x0A, 0x01, 0xFE, 0x01, 0xFE, 0, 0, 0, 0]))
		time.sleep(SETTLE)
		self.set_dpi(DEFAULT_DPI)
		self.set_lighting(DEFAULT_LIGHTING)
		self.set_polling_rate(1000)
		self.set_buttons(DEFAULT_BUTTONS)


# --- DPI encoding ------------------------------------------------------------

def dpi_to_bytes(dpi):
	"""Encode a DPI value the way the mouse stores it.

	Above 10000 the sensor halves the value and sets a "double" flag. The
	upstream driver used a separate two-byte mode here, which the mouse decodes
	as something else entirely: writing 22000 read back as 17700.
	"""
	dpi = max(50, min(int(dpi), 26000))
	if dpi == 20100:
		return 0xEB, 1, True
	double = dpi > 10000
	target = round(dpi / 2) if double else dpi
	if target > 5000 and target % 100 == 0:
		index, y_byte = target // 100 - 1, 1
	elif target % 50 == 0 and (target - 50) // 50 < USABLE_STEPS:
		index, y_byte = (target - 50) // 50, 0
	else:
		# Past the 50-step range only whole hundreds exist; snap to the nearest.
		index, y_byte = round(target / 100) - 1, 1
	return DPI_3311[max(0, min(index, len(DPI_3311) - 1))], y_byte, double


def bytes_to_dpi(x_byte, y_byte, double):
	if x_byte == 0 and y_byte == 0:
		return 0
	if y_byte in (0, 1):
		step = 50 if y_byte == 0 else 100
		value = (DPI_3311.index(x_byte) + 1) * step if x_byte in DPI_3311 else step
	else:
		value = 10100 + ((y_byte << 8 | x_byte) - 199) * 100
	if double:
		if x_byte == 235:
			return 20100
		value *= 2
	return min(value, 26000)


DEFAULT_DPI = {
	'values': [800, 1600, 2400, 3200, 5000, 22000, 0, 0],
	'colors': [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
	           (0, 255, 255), (255, 0, 255), (255, 64, 0), (255, 255, 255)],
	'current_stage': 2,
	'active_stages': 0x3F,
	'angle_snap': False,
	'ripple_control': True,
}

DEFAULT_LIGHTING = {
	'mode': 0, 'rgb': (0, 255, 0), 'speed': 3, 'brightness': 8,
	'sleep_time': 0.5, 'deep_sleep_time': 10, 'key_response': 8,
}

DEFAULT_BUTTONS = {
	1: [0x02, 0, 0], 2: [0x03, 0, 0], 3: [0x04, 0, 0], 4: [0x01, 0, 0], 5: [0x01, 0, 0],
	6: [0x0D, 0, 0], 7: [0x06, 0, 0], 8: [0x05, 0, 0],
	**{n: [0x01, 0, 0] for n in range(9, 17)},
	17: [0x09, 0, 0], 18: [0x0A, 0, 0],
}


def _checksum(buf, start, end):
	return sum(buf[start:end + 1]) & 0xFFFF


def build_dpi(s):
	buf = bytearray(LEN_DPI)
	buf[0], buf[1], buf[2] = REPORT_DPI, LEN_DPI, 0x01
	buf[3] = (0 << 4) | (1 if s['angle_snap'] else 0)
	buf[4] = (0 << 4) | (1 if s['ripple_control'] else 0)
	buf[5] = s['active_stages'] & 0xFF
	for i, value in enumerate(s['values'][:8]):
		if value:
			x, y, double = dpi_to_bytes(value)
		else:
			x, y, double = 0, 0, False
		buf[8 + i], buf[16 + i] = x, y
		if double:
			# The mouse sets the flag in both bytes; X and Y share the multiplier.
			buf[6] |= 1 << i
			buf[7] |= 1 << i
	buf[24] = s['current_stage']
	for i, (r, g, b) in enumerate(s['colors'][:8]):
		buf[25 + i * 3], buf[26 + i * 3], buf[27 + i * 3] = r & 0xFF, g & 0xFF, b & 0xFF
	buf[49] = 0x02
	total = _checksum(buf, 3, 49)
	buf[50], buf[51] = (total >> 8) & 0xFF, total & 0xFF
	return bytes(buf)


def parse_dpi(data):
	if len(data) != LEN_DPI:
		raise DeviceError(f'DPI packet was {len(data)} bytes, expected {LEN_DPI}')
	expected = (data[50] << 8) | data[51]
	if expected != _checksum(data, 3, 49):
		raise DeviceError('DPI packet failed its checksum')
	doubles = data[6]
	return {
		'angle_snap': bool(data[3] & 0x0F),
		'ripple_control': bool(data[4] & 0x0F),
		'active_stages': data[5],
		'current_stage': data[24],
		'values': [bytes_to_dpi(data[8 + i], data[16 + i], bool(doubles & (1 << i))) for i in range(8)],
		'colors': [(data[25 + i * 3], data[26 + i * 3], data[27 + i * 3]) for i in range(8)],
	}


def build_lighting(s):
	buf = bytearray(LEN_LIGHTING)
	buf[0], buf[1], buf[2] = REPORT_LIGHTING, LEN_LIGHTING, 0x01
	buf[3] = (int(s['mode']) & 0x0F) << 4
	deep = int(s['deep_sleep_time'])
	buf[4] = (deep & 0xF0) | (int(s['speed']) & 0x0F)
	buf[5] = ((deep & 0x0F) << 4) | (int(s['brightness']) & 0x0F)
	buf[6], buf[7], buf[8] = (c & 0xFF for c in s['rgb'])
	buf[9] = int(round(float(s['sleep_time']) * 2)) & 0xFF
	buf[10] = int(s['key_response']) // 2
	total = _checksum(buf, 3, 10)
	buf[11], buf[12] = (total >> 8) & 0xFF, total & 0xFF
	return bytes(buf)


def parse_lighting(data):
	if len(data) != LEN_LIGHTING:
		raise DeviceError(f'Lighting packet was {len(data)} bytes, expected {LEN_LIGHTING}')
	expected = (data[11] << 8) | data[12]
	if expected != _checksum(data, 3, 10):
		raise DeviceError('Lighting packet failed its checksum')
	deep = (((data[4] & 0xF0) >> 4) << 4) | ((data[5] & 0xF0) >> 4)
	sleep_index = data[9] - 1
	return {
		'mode': data[3] >> 4,
		'speed': data[4] & 0x0F,
		'brightness': data[5] & 0x0F,
		'rgb': (data[6], data[7], data[8]),
		'sleep_time': SLEEP_STEPS[sleep_index] if 0 <= sleep_index < len(SLEEP_STEPS) else 0.5,
		'deep_sleep_time': deep,
		'key_response': data[10] * 2,
	}


# --- buttons and macros ------------------------------------------------------

def build_buttons(slots, profile_id=1):
	buf = bytearray(LEN_BUTTONS)
	buf[0], buf[1], buf[2] = REPORT_BUTTONS, LEN_BUTTONS, profile_id
	for slot in range(1, 19):
		action, modifiers, usage = slots.get(slot) or [0x01, 0x00, 0x00]
		offset = 3 + (slot - 1) * 3
		buf[offset], buf[offset + 1], buf[offset + 2] = action & 0xFF, modifiers & 0xFF, usage & 0xFF
	total = _checksum(buf, 3, 56)
	buf[57], buf[58] = (total >> 8) & 0xFF, total & 0xFF
	return bytes(buf)


def parse_buttons(data):
	if len(data) != LEN_BUTTONS:
		raise DeviceError(f'Button packet was {len(data)} bytes, expected {LEN_BUTTONS}')
	expected = (data[57] << 8) | data[58]
	if expected != _checksum(data, 3, 56):
		raise DeviceError('Button packet failed its checksum')
	# Slots come back at the offsets they are written to, despite what the
	# upstream notes say; verified against an untouched X11.
	return {slot: [data[3 + (slot - 1) * 3], data[4 + (slot - 1) * 3], data[5 + (slot - 1) * 3]]
	        for slot in range(1, 19)}


def _encode_delay(ms):
	"""7-bit delay field; anything longer gets a second 200 ms-per-unit event."""
	def step(value):
		return min(2 * ((value + 5) // 20) + 1, 0x7F)

	if ms <= 1270:
		return step(ms), None
	return step(ms % 200), min(ms // 200, 0xFF)


def encode_macro_events(events):
	"""Steps are not slots: a delay over 1270 ms needs a second slot of its own,
	so a macro of long pauses fills the packet twice as fast."""
	out = bytearray()
	for event in events:
		delay, extra = _encode_delay(int(event.get('delayMs', 10)))
		out += bytes([0x80 | delay if event.get('release') else delay, int(event['code']) & 0xFF])
		if extra:
			out += bytes([extra, 0x03])
	if len(out) // 2 > MAX_MACRO_EVENTS:
		raise DeviceError(
			f'This macro needs {len(out) // 2} of the {MAX_MACRO_EVENTS} slots the mouse has. '
			'Remove some steps, or shorten waits longer than 1.2 seconds.')
	return bytes(out)


def build_macro_packets(button, events, mode=0, repeat=1):
	"""Three 0x09 packets: events page 0, events page 1, footer with the checksum."""
	if not 1 <= repeat <= 255:
		raise DeviceError('Repeat count must be between 1 and 255')
	page0, page1, footer = bytearray(64), bytearray(64), bytearray(64)
	page0[0], page0[1], page0[2], page0[3] = 0x09, 0x40, button, 0x00
	page0[4] = mode
	page0[8] = repeat
	page1[0], page1[1], page1[2], page1[3] = 0x09, 0x40, button, 0x01
	footer[0], footer[1], footer[2], footer[3] = 0x09, 0x0C, button, 0x02

	payload = encode_macro_events(events)
	page0[29] = len(payload) // 2
	first = payload[:34]
	page0[30:30 + len(first)] = first
	rest = payload[34:94]
	page1[4:4 + len(rest)] = rest

	# From byte 4, like the vendor software (0x4460de): starting at byte 8 left out
	# the play mode, so any mode but 0 failed the checksum and the mouse dropped it.
	total = (sum(page0[4:64]) + sum(page1[4:64])) & 0xFFFF
	footer[10], footer[11] = (total >> 8) & 0xFF, total & 0xFF
	return bytes(page0), bytes(page1), bytes(footer)
