"""Pointer settings applied through Hyprland (they belong to the compositor, not the mouse)."""
import json
import os
import re
import subprocess

from store import CONFIG_DIR

CONF_FILE = os.path.join(CONFIG_DIR, 'hyprland.conf')
SOURCE_LINE = f'source = {CONF_FILE}'

DEFAULTS = {'sensitivity': 0.0, 'accel_profile': 'default', 'scroll_factor': 1.0,
            'natural_scroll': False, 'left_handed': False}
ACCEL_PROFILES = ['default', 'flat', 'adaptive']


def available():
	return bool(os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')) and bool(_hyprctl(['version']))


def _hyprctl(args):
	try:
		out = subprocess.run(['hyprctl', *args], capture_output=True, text=True, timeout=5)
		return out.stdout if out.returncode == 0 else ''
	except (OSError, subprocess.SubprocessError):
		return ''


def mouse_devices():
	"""Pointer devices, best guess for the mouse first."""
	try:
		devices = json.loads(_hyprctl(['devices', '-j']) or '{}').get('mice', [])
	except json.JSONDecodeError:
		return []
	names = [m.get('name', '') for m in devices if m.get('name')]
	names.sort(key=lambda n: ('consumer-control' in n or 'keyboard' in n, n))
	return names


def _numeric_option(option):
	"""Raw text of a numeric Hyprland option, or None when it can't be read."""
	for line in _hyprctl(['getoption', option]).splitlines():
		if line.startswith(('float:', 'int:')):
			return line.split(':', 1)[1].strip()
	return None


def _accel_profile():
	profile = ''
	for line in _hyprctl(['getoption', 'input:accel_profile']).splitlines():
		if line.startswith('str:'):
			profile = line.split(':', 1)[1].strip().strip('[]')
	return profile if profile in ACCEL_PROFILES else 'default'


def global_settings():
	"""Hyprland's current global input settings, used as the starting point."""
	settings = dict(DEFAULTS)
	for key, option, kind in (
		('sensitivity', 'input:sensitivity', float),
		('scroll_factor', 'input:scroll_factor', float),
		('natural_scroll', 'input:natural_scroll', bool),
		('left_handed', 'input:left_handed', bool),
	):
		raw = _numeric_option(option)
		if raw is None:
			continue
		try:
			settings[key] = bool(int(float(raw))) if kind is bool else kind(float(raw))
		except ValueError:
			pass
	settings['accel_profile'] = _accel_profile()
	return settings


def _safe_device(device):
	"""Device names come from the compositor, but a peripheral picks its own name;
	without this a crafted name could inject lines into the config file."""
	if not re.fullmatch(r'[A-Za-z0-9 ._:+-]{1,128}', device or ''):
		raise ValueError(f'Refusing to use an unusual device name: {device!r}')
	return device


def _keywords(device, settings):
	device = _safe_device(device)
	pairs = [
		('sensitivity', settings['sensitivity']),
		('scroll_factor', settings['scroll_factor']),
		('natural_scroll', int(bool(settings['natural_scroll']))),
		('left_handed', int(bool(settings['left_handed']))),
	]
	if settings['accel_profile'] != 'default':
		pairs.append(('accel_profile', settings['accel_profile']))
	return [(f'device[{device}]:{key}', value) for key, value in pairs]


def apply(device, settings):
	"""Apply now, for this Hyprland session only."""
	failures = []
	for key, value in _keywords(device, settings):
		if not _hyprctl(['keyword', key, str(value)]).strip().startswith('ok'):
			failures.append(key)
	if failures:
		raise RuntimeError('Hyprland rejected: ' + ', '.join(failures))


def write_conf(device, settings):
	"""Persist to our own config file; Hyprland reads it only if the user sources it."""
	device = _safe_device(device)
	os.makedirs(CONFIG_DIR, exist_ok=True)
	lines = ['# Written by the Attack Shark X11 app.', 'device {', f'    name = {device}']
	for key, value in _keywords(device, settings):
		lines.append(f'    {key.split(":", 1)[1]} = {value}')
	lines.append('}')
	with open(CONF_FILE, 'w') as f:
		f.write('\n'.join(lines) + '\n')
	return CONF_FILE


def is_sourced():
	"""True when some Hyprland config already sources our file."""
	root = os.path.expanduser('~/.config/hypr')
	for dirpath, _dirs, files in os.walk(root):
		for name in files:
			if not name.endswith('.conf'):
				continue
			try:
				with open(os.path.join(dirpath, name)) as f:
					if CONF_FILE in f.read():
						return True
			except OSError:
				continue
	return False
