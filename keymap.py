"""Key names, HID usage ids and the firmware's button actions."""

# GDK key name -> HID keyboard usage id
KEY_TO_USAGE = {}
for _i, _c in enumerate('abcdefghijklmnopqrstuvwxyz'):
	KEY_TO_USAGE[_c] = 0x04 + _i
for _i, _c in enumerate('123456789'):
	KEY_TO_USAGE[_c] = 0x1E + _i
KEY_TO_USAGE['0'] = 0x27
KEY_TO_USAGE.update({
	'Return': 0x28, 'Escape': 0x29, 'BackSpace': 0x2A, 'Tab': 0x2B, 'space': 0x2C,
	'minus': 0x2D, 'equal': 0x2E, 'bracketleft': 0x2F, 'bracketright': 0x30, 'backslash': 0x31,
	'semicolon': 0x33, 'apostrophe': 0x34, 'grave': 0x35, 'comma': 0x36, 'period': 0x37,
	'slash': 0x38, 'Caps_Lock': 0x39, 'Print': 0x46, 'Scroll_Lock': 0x47, 'Pause': 0x48,
	'Insert': 0x49, 'Home': 0x4A, 'Page_Up': 0x4B, 'Delete': 0x4C, 'End': 0x4D, 'Page_Down': 0x4E,
	'Right': 0x4F, 'Left': 0x50, 'Down': 0x51, 'Up': 0x52, 'Num_Lock': 0x53,
	'KP_Divide': 0x54, 'KP_Multiply': 0x55, 'KP_Subtract': 0x56, 'KP_Add': 0x57, 'KP_Enter': 0x58,
	'KP_End': 0x59, 'KP_Down': 0x5A, 'KP_Page_Down': 0x5B, 'KP_Left': 0x5C, 'KP_Begin': 0x5D,
	'KP_Right': 0x5E, 'KP_Home': 0x5F, 'KP_Up': 0x60, 'KP_Page_Up': 0x61, 'KP_Insert': 0x62,
	'KP_Delete': 0x63, 'Menu': 0x65,
	'Control_L': 0xE0, 'Shift_L': 0xE1, 'Alt_L': 0xE2, 'Super_L': 0xE3,
	'Control_R': 0xE4, 'Shift_R': 0xE5, 'Alt_R': 0xE6, 'Super_R': 0xE7,
})
for _i in range(1, 13):
	KEY_TO_USAGE[f'F{_i}'] = 0x39 + _i

USAGE_TO_KEY = {v: k for k, v in KEY_TO_USAGE.items()}

# Modifier bits the firmware expects alongside a keyboard action
MODIFIER_BITS = {
	'Control_L': 0x01, 'Control_R': 0x01,
	'Shift_L': 0x02, 'Shift_R': 0x02,
	'Alt_L': 0x04, 'Alt_R': 0x04,
	'Super_L': 0x08, 'Super_R': 0x08,
}
MODIFIER_NAMES = [(0x01, 'Ctrl'), (0x02, 'Shift'), (0x04, 'Alt'), (0x08, 'Super')]

MOUSE_MACRO_CODES = [(0xF1, 'Left click'), (0xF2, 'Right click'), (0xF3, 'Middle click'),
                     (0xF4, 'Back click'), (0xF5, 'Forward click')]

DISABLED = 0x01
KEYBOARD_ACTION = 0x11
MACRO_ACTION = 0x12

EASY_AIM = 0x10
LED_CYCLE = 0x29

# Firmware action id -> label, in the order shown in the dropdown. These match the
# actions the vendor software offers for the X11.
ACTIONS = [
	(0x02, 'Left click'), (0x03, 'Right click'), (0x04, 'Middle click'),
	(0x06, 'Forward'), (0x05, 'Back'), (0x07, 'Double click'), (0x08, 'Rapid fire'),
	(0x09, 'Scroll up'), (0x0A, 'Scroll down'),
	(0x0D, 'DPI cycle'), (0x0E, 'DPI up'), (0x0F, 'DPI down'), (EASY_AIM, 'Easy aim'),
	(KEYBOARD_ACTION, 'Keyboard key'), (MACRO_ACTION, 'Macro'),
	(0x18, 'Play / pause'), (0x17, 'Next track'), (0x16, 'Previous track'), (0x19, 'Stop'),
	(0x1A, 'Mute'), (0x1B, 'Volume up'), (0x1C, 'Volume down'), (0x15, 'Media player'),
	(0x1D, 'Calculator'), (0x1E, 'Email'), (0x23, 'My computer'),
	(0x20, 'Browser forward'), (0x21, 'Browser back'), (0x22, 'Browser stop'),
	(0x24, 'Browser refresh'), (0x25, 'Browser home'), (0x26, 'Browser search'),
	(DISABLED, 'Disabled'),
]

# Recognised when read back from the mouse but not offered. The vendor software
# has the others in its table but hides them for the X11; 0x40 came from the
# upstream driver and isn't in the vendor's table at all.
HIDDEN_ACTIONS = [(0x0B, 'Scroll left'), (0x0C, 'Scroll right'), (LED_CYCLE, 'Lighting mode cycle'),
                  (0x40, 'Polling rate cycle')]

ACTION_LABELS = dict(ACTIONS + HIDDEN_ACTIONS)

# The third byte the vendor software sends with these actions (see protocol-reference).
ACTION_PARAMS = {EASY_AIM: 0x03, LED_CYCLE: 0x03}


def describe_slot(slot):
	"""Human-readable summary of an [action, modifiers, usage] triple."""
	action, modifiers, usage = slot
	if action == KEYBOARD_ACTION:
		parts = [name for bit, name in MODIFIER_NAMES if modifiers & bit]
		key = USAGE_TO_KEY.get(usage, f'0x{usage:02x}')
		parts.append(key.replace('_', ' ') if len(key) > 1 else key.upper())
		return ' + '.join(parts)
	if action == MACRO_ACTION:
		return 'Macro'
	return ACTION_LABELS.get(action, f'Unknown (0x{action:02x})')
