#!/usr/bin/env python3
"""Attack Shark X11 control app (GTK4 + libadwaita).

The UI runs on the main thread; every request to the mouse goes through a single
worker thread, because the device only handles one exchange at a time.
"""
import math
import os
import queue
import sys
import threading
import time

import gi

import device
import hypr
import service
import store
from keymap import (ACTIONS, ACTION_PARAMS, DISABLED, KEYBOARD_ACTION, KEY_TO_USAGE, MACRO_ACTION,
                    MODIFIER_BITS, MOUSE_MACRO_CODES, USAGE_TO_KEY, describe_slot)

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

APP_ID = 'dev.local.AttackSharkX11'
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ACCENT = '#ff7a2f'
CHARGE = '#ffc14d'

LIGHT_MODES = ['Off', 'Static', 'Breathing', 'Neon', 'Color cycle', 'DPI color', 'DPI breathing']
ANIMATED_MODES = {2, 3, 4, 6}
CUSTOM_COLOR_MODES = {1, 2}
# Macro play modes, in firmware order. The mouse can't see the keyboard, so its
# "until any key" means until another mouse button is clicked.
PLAY_MODES = ['Repeat a number of times', 'Repeat until another mouse button is clicked',
              'Repeat while held']
NOTIFY_SELECTED = 'notify::selected'
PRESET_COLORS = ['#ff3b3b', '#ff7a2f', '#ffe03b', '#3bff6a', '#3bf0ff', '#3b6bff', '#c77dff', '#ffffff']

# Slots the firmware exposes for the five physical buttons plus the DPI button.
PHYSICAL_BUTTONS = [(1, 'Left click'), (2, 'Right click'), (3, 'Middle click'),
                    (7, 'Forward (side)'), (8, 'Back (side)')]
MACRO_CAPABLE = {1, 2, 3, 7, 8}

CSS = f"""
window {{ font-family: Manrope, Cantarell, sans-serif; }}
.app-bg {{ background: #111317; color: #eceef2; }}
.sidebar {{ background: #0c0e11; border-right: 1px solid #22262e; padding: 24px 16px; }}
.brand-title {{ font-weight: 700; font-size: 15px; }}
.muted {{ color: #9aa0ad; }}
.small {{ font-size: 12px; }}
button.nav {{ min-height: 44px; padding: 0 12px; border-radius: 10px; background: transparent;
  color: #9aa0ad; font-weight: 500; box-shadow: none; border: none; }}
button.nav:hover {{ background: #161a20; }}
button.nav.active {{ background: #1c2027; color: #eceef2; font-weight: 600; }}
button.nav.active image {{ color: {ACCENT}; }}
.card {{ background: #161a20; border: 1px solid #22262e; border-radius: 14px; padding: 16px 20px; }}
.big-number {{ font-family: "JetBrains Mono", "JetBrainsMono Nerd Font", monospace; font-size: 32px; font-weight: 700; }}
.ok {{ color: #6fdc9a; }}
.err {{ color: #ff8a8a; }}
levelbar.battery trough {{ min-height: 6px; background: #262a33; border-radius: 3px; border: none; }}
levelbar.battery block.filled {{ background: #6fdc9a; border-radius: 3px; }}
levelbar.battery block.low {{ background: #ff8a8a; }}
levelbar.battery block.empty {{ background: transparent; }}
.big-word {{ font-size: 26px; font-weight: 700; }}
.charge {{ color: {CHARGE}; }}
.card.charging {{ border-color: {CHARGE}; }}
@keyframes charge-pulse {{ from {{ opacity: 1; }} to {{ opacity: 0.35; }} }}
levelbar.battery.charging block.filled {{ background: {CHARGE}; animation: charge-pulse 1.2s ease-in-out infinite alternate; }}
.page {{ padding: 32px 40px; }}
.page-title {{ font-size: 26px; font-weight: 700; }}
.section-title {{ font-size: 15px; font-weight: 600; }}
button.apply {{ background: {ACCENT}; color: #1a0e05; font-weight: 700; border-radius: 10px;
  min-height: 44px; padding: 0 24px; border: none; box-shadow: none; }}
button.apply:disabled {{ background: #3a2a20; color: #7d6a5c; }}
button.ghost {{ background: transparent; border: 1px solid #2c313b; color: #eceef2; font-weight: 600;
  border-radius: 10px; min-height: 44px; padding: 0 18px; box-shadow: none; }}
button.danger {{ background: transparent; border: 1px solid #5a2a2a; color: #ff8a8a; font-weight: 600;
  border-radius: 10px; min-height: 44px; padding: 0 18px; box-shadow: none; }}
.stage-card {{ background: #161a20; border: 1px solid #22262e; border-radius: 14px; padding: 16px; }}
.stage-card.active {{ background: #1b1f26; border: 2px solid {ACCENT}; padding: 15px; }}
.stage-card.disabled {{ opacity: 0.45; }}
.active-tag {{ color: {ACCENT}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
spinbutton.dpi-value {{ font-family: "JetBrains Mono", "JetBrainsMono Nerd Font", monospace; font-size: 24px;
  font-weight: 700; background: transparent; border: none; box-shadow: none; outline: none; }}
spinbutton.dpi-value button {{ background: transparent; border: none; box-shadow: none; color: #9aa0ad; min-width: 28px; }}
spinbutton.field {{ font-family: "JetBrains Mono", "JetBrainsMono Nerd Font", monospace; }}
.segmented {{ background: #0e1014; border-radius: 12px; padding: 4px; }}
.segmented togglebutton {{ min-height: 40px; padding: 0 16px; border-radius: 9px; background: transparent;
  color: #9aa0ad; font-weight: 600; border: none; box-shadow: none; }}
.segmented togglebutton:hover {{ background: #171a20; }}
.segmented togglebutton:checked {{ background: #262b34; color: #eceef2; font-weight: 700; }}
button.swatch {{ min-width: 36px; min-height: 36px; padding: 0; border-radius: 50%; border: 3px solid transparent; box-shadow: none; }}
button.swatch.selected {{ border-color: #eceef2; }}
flowbox.segmented flowboxchild, flowbox.card flowboxchild {{ padding: 0; background: none; }}
flowbox flowboxchild {{ padding: 0; }}
""" + "".join(f"button.swatch-{n} {{ background: {c}; }}\n" for n, c in enumerate(PRESET_COLORS)) + f"""
scale highlight {{ background: {ACCENT}; }}
row.setting {{ background: #161a20; border: 1px solid #22262e; border-radius: 12px; padding: 10px 14px; }}
list.plain {{ background: none; }}
list.plain > row {{ background: #161a20; border: 1px solid #22262e; border-radius: 12px; margin-bottom: 8px; padding: 8px 12px; }}
list.plain > row:selected {{ background: #1b1f26; border-color: {ACCENT}; }}
.event-row {{ background: #14171d; border-radius: 10px; padding: 6px 10px; }}
.mono {{ font-family: "JetBrains Mono", "JetBrainsMono Nerd Font", monospace; }}
scale slider {{ background: #eceef2; }}
switch:checked {{ background: {ACCENT}; }}
"""


# --- worker ------------------------------------------------------------------

class DeviceWorker:
	"""Runs device requests one at a time on a background thread."""

	def __init__(self, on_busy_changed):
		self._q = queue.Queue()
		self._busy = 0
		self._on_busy_changed = on_busy_changed
		threading.Thread(target=self._loop, daemon=True).start()

	@property
	def busy(self):
		return self._busy > 0

	def submit(self, work, on_done):
		self._busy += 1
		self._on_busy_changed(True)
		self._q.put((work, on_done))

	def _finish(self, on_done, result, error):
		self._busy -= 1
		if self._busy == 0:
			self._on_busy_changed(False)
		on_done(result, error)
		return False

	def _loop(self):
		while True:
			work, on_done = self._q.get()
			try:
				GLib.idle_add(self._finish, on_done, work(), None)
			except Exception as e:  # noqa: BLE001
				GLib.idle_add(self._finish, on_done, None, e)


# --- helpers ---------------------------------------------------------------

def rgb_to_rgba(colour):
	r, g, b = colour
	rgba = Gdk.RGBA()
	rgba.red, rgba.green, rgba.blue, rgba.alpha = r / 255, g / 255, b / 255, 1.0
	return rgba


def rgba_to_rgb(rgba):
	return (round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255))


def hex_to_rgb(h):
	return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))


def box(orientation=Gtk.Orientation.VERTICAL, spacing=0, css=None, **kw):
	b = Gtk.Box(orientation=orientation, spacing=spacing, **kw)
	for c in (css or []):
		b.add_css_class(c)
	return b


def trimmed_pixbuf(path, height):
	"""The image cropped to its visible pixels and scaled to `height`. The logo
	file carries wide transparent margins (it doubles as the square-ish menu
	icon), which would otherwise push everything beside it sideways."""
	pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
	width, rows, stride, channels = (pixbuf.get_width(), pixbuf.get_height(),
	                                 pixbuf.get_rowstride(), pixbuf.get_n_channels())
	if pixbuf.get_has_alpha():
		pixels = pixbuf.get_pixels()
		seen = [(x, y) for y in range(rows) for x in range(width)
		        if pixels[y * stride + x * channels + 3] > 16]
		if seen:
			left, right = min(x for x, _ in seen), max(x for x, _ in seen)
			top, bottom = min(y for _, y in seen), max(y for _, y in seen)
			pixbuf = pixbuf.new_subpixbuf(left, top, right - left + 1, bottom - top + 1)
	scale = height / pixbuf.get_height()
	return pixbuf.scale_simple(max(1, round(pixbuf.get_width() * scale)), height,
	                           GdkPixbuf.InterpType.BILINEAR)


def label(text, css=None, xalign=0.0, **kw):
	lbl = Gtk.Label(label=text, xalign=xalign, **kw)
	for c in (css or []):
		lbl.add_css_class(c)
	return lbl


def button_widget(text, css=None, on_click=None):
	b = Gtk.Button(label=text)
	for c in (css or []):
		b.add_css_class(c)
	if on_click:
		b.connect('clicked', lambda _w: on_click())
	return b


def segmented(options, on_toggle, wrap=False):
	if wrap:
		row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=4, row_spacing=4,
			min_children_per_line=2, max_children_per_line=len(options), halign=Gtk.Align.START)
		row.add_css_class('segmented')
	else:
		row = box(Gtk.Orientation.HORIZONTAL, 4, ['segmented'], halign=Gtk.Align.START)
	buttons, first = [], None
	for i, text in enumerate(options):
		tb = Gtk.ToggleButton(label=text)
		if first:
			tb.set_group(first)
		else:
			first = tb
		tb.connect('toggled', lambda w, i=i: w.get_active() and on_toggle(i))
		row.append(tb)
		buttons.append(tb)
	return row, buttons


def switch_row(parent, title, hint, on_change):
	row = box(Gtk.Orientation.HORIZONTAL, 16)
	titles = box(spacing=2, hexpand=True)
	titles.append(label(title))
	titles.append(label(hint, ['muted', 'small'], wrap=True))
	row.append(titles)
	sw = Gtk.Switch(valign=Gtk.Align.CENTER)
	sw.connect('notify::active', lambda *_: on_change())
	row.append(sw)
	parent.append(row)
	return sw


def page_header(title, subtitle, on_revert, on_apply):
	header = box(Gtk.Orientation.HORIZONTAL, 12)
	titles = box(spacing=4, hexpand=True)
	titles.append(label(title, ['page-title']))
	titles.append(label(subtitle, ['muted'], wrap=True))
	header.append(titles)
	revert = button_widget('Revert', ['ghost'], on_revert)
	apply = button_widget('Apply', ['apply'], on_apply)
	for b in (revert, apply):
		b.set_valign(Gtk.Align.CENTER)
		header.append(b)
	return header, apply


# --- Performance page ---------------------------------------------------------

class StageCard(Gtk.Box):
	def __init__(self, index, page):
		super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
		self.add_css_class('stage-card')
		self.index, self.page = index, page
		self._color = (255, 255, 255)

		top = box(Gtk.Orientation.HORIZONTAL, 8)
		self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
		self.color_btn.set_tooltip_text('Stage color')
		self.color_btn.connect('notify::rgba', self._on_color)
		top.append(self.color_btn)
		top.append(label(f'Stage {index + 1}', ['muted'], hexpand=True))
		self.active_tag = label('ACTIVE', ['active-tag'])
		top.append(self.active_tag)
		self.enabled = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text='Include in DPI button cycle')
		self.enabled.connect('notify::active', self._on_enabled)
		top.append(self.enabled)
		self.append(top)

		self.spin = Gtk.SpinButton.new_with_range(50, 26000, 50)
		self.spin.add_css_class('dpi-value')
		self.spin.connect('value-changed', lambda _w: (self.bar.queue_draw(), page.mark_dirty()))
		self.append(self.spin)

		self.bar = Gtk.DrawingArea(content_height=4)
		self.bar.set_draw_func(self._draw_bar)
		self.append(self.bar)

		click = Gtk.GestureClick()
		click.connect('released', lambda *_: page.set_active_stage(index))
		self.add_controller(click)
		self._loading = False

	def load(self, value, color, enabled, active):
		self._loading = True
		self._color = tuple(color)
		self._loaded_value = value
		self.spin.set_value(value if value > 0 else 800)
		self.color_btn.set_rgba(rgb_to_rgba(color))
		self.enabled.set_active(enabled)
		self._loading = False
		self.refresh(active)

	def refresh(self, active):
		is_on = self.enabled.get_active()
		self.active_tag.set_visible(active)
		self.spin.set_sensitive(is_on)
		for cls, on in (('active', active), ('disabled', not is_on)):
			(self.add_css_class if on else self.remove_css_class)(cls)
		self.bar.queue_draw()

	def _on_color(self, *_):
		self._color = rgba_to_rgb(self.color_btn.get_rgba())
		self.bar.queue_draw()
		if not self._loading:
			self.page.mark_dirty()

	def _on_enabled(self, *_):
		if not self._loading:
			self.page.on_stage_toggled(self.index)

	def _draw_bar(self, _area, cr, width, height):
		frac = (math.log(max(self.spin.get_value(), 50)) - math.log(50)) / (math.log(26000) - math.log(50))
		cr.set_source_rgb(0x26 / 255, 0x2a / 255, 0x33 / 255)
		cr.rectangle(0, 0, width, height)
		cr.fill()
		r, g, b = self._color
		cr.set_source_rgb(r / 255, g / 255, b / 255)
		cr.rectangle(0, 0, width * frac, height)
		cr.fill()

	@property
	def value(self):
		# A stage that was unset and is still switched off stays unset.
		if getattr(self, '_loaded_value', None) == 0 and not self.enabled.get_active():
			return 0
		return int(self.spin.get_value())


class PerformancePage(Gtk.Box):
	loaded = False  # nothing can be applied until the mouse has been read

	RATES = [125, 250, 500, 1000]

	def __init__(self, win):
		super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=28)
		self.add_css_class('page')
		self.win = win
		self.current_stage = 1
		self.loaded_rate = None
		self._loading = False

		header, self.apply_btn = page_header('Performance', 'Sensitivity stages and report rate', self.load, self.apply)
		self.append(header)

		dpi_section = box(spacing=14)
		dpi_section.append(label('DPI stages', ['section-title']))
		self.enabled_hint = label('', ['muted', 'small'], wrap=True)
		dpi_section.append(self.enabled_hint)

		grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, column_spacing=14,
			row_spacing=14, min_children_per_line=2, max_children_per_line=4)
		self.cards = [StageCard(i, self) for i in range(8)]
		for card in self.cards:
			grid.append(card)
		dpi_section.append(grid)
		self.append(dpi_section)

		rate_card = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=1,
			max_children_per_line=2, column_spacing=12, row_spacing=12)
		rate_card.add_css_class('card')
		titles = box(spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
		titles.append(label('Polling rate', ['section-title']))
		titles.append(label('How often the mouse reports its position', ['muted', 'small'], wrap=True))
		rate_card.append(titles)
		seg, self.rate_buttons = segmented([f'{r} Hz' for r in self.RATES], lambda _i: self.mark_dirty())
		seg.set_valign(Gtk.Align.CENTER)
		rate_card.append(seg)
		self.append(rate_card)

		sensor = box(spacing=16, css=['card'])
		sensor.append(label('Sensor', ['section-title']))
		self.angle_snap = switch_row(sensor, 'Angle snap',
			'Straightens near-horizontal and near-vertical movement. Usually off outside drawing.', self.mark_dirty)
		self.ripple = switch_row(sensor, 'Ripple control',
			'Smooths out small jitter in the sensor readings.', self.mark_dirty)
		self.append(sensor)

		self.apply_btn.set_sensitive(False)

	# state
	def mark_dirty(self):
		# Requests queue on the worker, so staying enabled while it is busy is safe.
		if not self._loading and self.loaded:
			self.apply_btn.set_sensitive(True)

	def set_active_stage(self, index):
		if not self.cards[index].enabled.get_active() or self.current_stage == index + 1:
			return
		self.current_stage = index + 1
		self._refresh_cards()
		self.mark_dirty()

	def on_stage_toggled(self, index):
		card = self.cards[index]
		if not card.enabled.get_active() and (index + 1 == self.current_stage
				or not any(c.enabled.get_active() for c in self.cards)):
			self.win.toast('The active stage can’t be turned off — pick another stage first')
			card._loading = True
			card.enabled.set_active(True)
			card._loading = False
			return
		self._refresh_cards()
		self.mark_dirty()

	def _refresh_cards(self):
		for i, c in enumerate(self.cards):
			c.refresh(i + 1 == self.current_stage)
		n = sum(c.enabled.get_active() for c in self.cards)
		self.enabled_hint.set_text(f'Click a card to make it active · {n} of 8 in the DPI button cycle')

	# device I/O
	def load(self):
		self.win.worker.submit(service.get_performance, self._on_loaded)

	def _on_loaded(self, result, error):
		if error:
			self.win.load_failed(self, 'DPI settings', error)
			return
		self.loaded = True
		self.win.unread.discard(self)
		dpi, rate = result
		self._loading = True
		self.current_stage = dpi['current_stage']
		for i, card in enumerate(self.cards):
			card.load(dpi['values'][i], tuple(dpi['colors'][i]), bool(dpi['active_stages'] & (1 << i)), i + 1 == self.current_stage)
		self.angle_snap.set_active(dpi['angle_snap'])
		self.ripple.set_active(dpi['ripple_control'])
		self.loaded_rate = rate
		if rate in self.RATES:
			self.rate_buttons[self.RATES.index(rate)].set_active(True)
		self._refresh_cards()
		self._loading = False
		self._dirty = False
		self.apply_btn.set_sensitive(False)

	def apply(self):
		values = [c.value for c in self.cards]
		colors = [c._color for c in self.cards]
		stage = self.current_stage
		mask = sum(1 << i for i, c in enumerate(self.cards) if c.enabled.get_active())
		angle, ripple = self.angle_snap.get_active(), self.ripple.get_active()
		rate = next((self.RATES[i] for i, b in enumerate(self.rate_buttons) if b.get_active()), None)
		wanted_rate = rate if rate != self.loaded_rate else None

		def work():
			return service.set_performance(values, colors, stage, mask, angle, ripple, wanted_rate)

		def done(result, error):
			if error:
				self.win.toast(f'Couldn’t apply: {error}')
				return
			self._on_loaded(result, None)
			self.win.toast('Performance settings applied')

		self.apply_btn.set_sensitive(False)
		self.win.worker.submit(work, done)


# --- Lighting page -----------------------------------------------------------

class LightingPage(Gtk.Box):
	loaded = False  # nothing can be applied until the mouse has been read

	def __init__(self, win):
		super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=24)
		self.add_css_class('page')
		self.win = win
		self._loading = False
		self.mode = 0

		header, self.apply_btn = page_header('Lighting', 'Logo LED effect, color and brightness', self.load, self.apply)
		self.append(header)

		mode_card = box(spacing=14, css=['card'])
		mode_card.append(label('Effect', ['section-title']))
		seg, self.mode_buttons = segmented(LIGHT_MODES, self._on_mode, wrap=True)
		mode_card.append(seg)
		self.append(mode_card)

		color_card = box(spacing=14, css=['card'])
		color_card.append(label('Color', ['section-title']))
		self.color_hint = label('', ['muted', 'small'])
		color_card.append(self.color_hint)
		swatches = box(Gtk.Orientation.HORIZONTAL, 10)
		self.swatch_buttons = []
		for n, hex_color in enumerate(PRESET_COLORS):
			b = Gtk.Button(tooltip_text=hex_color)
			b.add_css_class('swatch')
			b.add_css_class(f'swatch-{n}')
			b.connect('clicked', lambda _w, h=hex_color: self._set_color(hex_to_rgb(h), True))
			swatches.append(b)
			self.swatch_buttons.append((hex_color, b))
		self.custom_color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), tooltip_text='Custom color')
		self.custom_color.connect('notify::rgba', self._on_custom_color)
		swatches.append(self.custom_color)
		self.swatch_row = swatches
		color_card.append(swatches)
		self.append(color_card)

		adjust = box(spacing=16, css=['card'])
		self.brightness = self._scale_row(adjust, 'Brightness', 1, 8)
		self.speed = self._scale_row(adjust, 'Animation speed', 1, 5)
		self.append(adjust)

		self.rgb = (255, 255, 255)
		self.apply_btn.set_sensitive(False)

	def _scale_row(self, parent, title, lo, hi):
		row = box(Gtk.Orientation.HORIZONTAL, 16)
		row.append(label(title, ['muted'], width_chars=16))
		scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
		scale.set_hexpand(True)
		scale.set_digits(0)
		scale.set_draw_value(True)
		scale.set_value_pos(Gtk.PositionType.RIGHT)
		for v in range(lo, hi + 1):
			scale.add_mark(v, Gtk.PositionType.BOTTOM, None)
		scale.connect('value-changed', lambda _w: self._dirty())
		row.append(scale)
		parent.append(row)
		return scale

	def _dirty(self):
		if not self._loading and self.loaded:
			self.apply_btn.set_sensitive(True)

	def _on_mode(self, i):
		self.mode = i
		self._update_sensitivity()
		self._dirty()

	def _update_sensitivity(self):
		custom = self.mode in CUSTOM_COLOR_MODES
		self.swatch_row.set_sensitive(custom)
		self.speed.set_sensitive(self.mode in ANIMATED_MODES)
		self.brightness.set_sensitive(self.mode != 0)
		if self.mode == 0:
			self.color_hint.set_text('Lighting is off.')
		elif self.mode in (5, 6):
			self.color_hint.set_text('Uses each DPI stage’s color — edit them on the Performance page.')
		elif not custom:
			self.color_hint.set_text('This effect cycles through colors on its own.')
		else:
			self.color_hint.set_text('Pick a preset or a custom color.')

	def _set_color(self, rgb, dirty):
		self.rgb = tuple(rgb)
		hex_now = '#{:02x}{:02x}{:02x}'.format(*self.rgb)
		for h, b in self.swatch_buttons:
			(b.add_css_class if h == hex_now else b.remove_css_class)('selected')
		if dirty:
			self._dirty()

	def _on_custom_color(self, *_):
		if not self._loading:
			self._set_color(rgba_to_rgb(self.custom_color.get_rgba()), True)

	def load(self):
		self.win.worker.submit(service.get_lighting, self._on_loaded)

	def _on_loaded(self, data, error):
		if error:
			self.win.load_failed(self, 'lighting settings', error)
			return
		self.loaded = True
		self.win.unread.discard(self)
		self._loading = True
		self.mode = data['mode']
		if 0 <= self.mode < len(self.mode_buttons):
			self.mode_buttons[self.mode].set_active(True)
		self.custom_color.set_rgba(rgb_to_rgba(tuple(data['rgb'])))
		self._set_color(tuple(data['rgb']), False)
		self.brightness.set_value(data['brightness'])
		self.speed.set_value(data['speed'])
		self._update_sensitivity()
		self._loading = False
		self.win.power_page.on_lighting_loaded(data)
		self.apply_btn.set_sensitive(False)

	def apply(self):
		changes = {
			'mode': self.mode,
			'rgb': self.rgb,
			'brightness': int(self.brightness.get_value()),
			'speed': int(self.speed.get_value()),
		}

		def done(data, error):
			if error:
				self.win.toast(f'Couldn’t apply lighting: {error}')
				return
			self._on_loaded(data, None)
			self.win.toast('Lighting applied')

		self.apply_btn.set_sensitive(False)
		self.win.worker.submit(lambda: service.set_lighting(**changes), done)


# --- Power page ----------------------------------------------------------------

class PowerPage(Gtk.Box):
	loaded = False  # nothing can be applied until the mouse has been read

	def __init__(self, win):
		super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=24)
		self.add_css_class('page')
		self.win = win
		self._loading = False

		header, self.apply_btn = page_header('Power & device', 'Sleep timers, click debounce and reset', win.lighting_page.load, self.apply)
		self.append(header)

		card = box(spacing=18, css=['card'])
		self.sleep = self._field(card, 'Sleep after', 'Idle time before the mouse dims and sleeps', 0.5, 30, 0.5, 1, 'min')
		self.deep = self._field(card, 'Deep sleep after', 'Idle time before the wireless link powers down', 1, 60, 1, 0, 'min')
		self.debounce = self._field(card, 'Click debounce', 'Lower is more responsive; raise it if clicks double-fire', 4, 50, 2, 0, 'ms')
		self.append(card)

		reset = box(Gtk.Orientation.HORIZONTAL, 16, ['card'])
		titles = box(spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
		titles.append(label('Factory reset', ['section-title']))
		titles.append(label('Restores DPI, lighting, polling rate, sleep and button mapping to defaults.', ['muted', 'small'], wrap=True))
		reset.append(titles)
		reset_btn = button_widget('Reset mouse', ['danger'], self.confirm_reset)
		reset_btn.set_valign(Gtk.Align.CENTER)
		reset.append(reset_btn)
		self.append(reset)

		self.apply_btn.set_sensitive(False)

	def _field(self, parent, title, hint, lo, hi, step, digits, unit):
		row = box(Gtk.Orientation.HORIZONTAL, 16)
		titles = box(spacing=2, hexpand=True)
		titles.append(label(title))
		titles.append(label(hint, ['muted', 'small'], wrap=True))
		row.append(titles)
		spin = Gtk.SpinButton.new_with_range(lo, hi, step)
		spin.set_digits(digits)
		spin.add_css_class('field')
		spin.set_valign(Gtk.Align.CENTER)
		spin.connect('value-changed', lambda _w: self._dirty())
		row.append(spin)
		row.append(label(unit, ['muted'], width_chars=3, valign=Gtk.Align.CENTER))
		parent.append(row)
		return spin

	def _dirty(self):
		if not self._loading and self.loaded:
			self.apply_btn.set_sensitive(True)

	def on_lighting_loaded(self, data):
		self.loaded = True
		self._loading = True
		self.sleep.set_value(data['sleep_time'])
		self.deep.set_value(data['deep_sleep_time'])
		self.debounce.set_value(data['key_response'])
		self._loading = False
		self.apply_btn.set_sensitive(False)

	def apply(self):
		changes = {
			'sleep_time': self.sleep.get_value(),
			'deep_sleep_time': int(self.deep.get_value()),
			'key_response': int(self.debounce.get_value()),
		}

		def done(data, error):
			if error:
				self.win.toast(f'Couldn’t apply: {error}')
				return
			self.win.lighting_page._on_loaded(data, None)
			self.win.toast('Power settings applied')

		self.apply_btn.set_sensitive(False)
		self.win.worker.submit(lambda: service.set_lighting(**changes), done)

	def confirm_reset(self):
		dialog = Adw.AlertDialog(
			heading='Reset the mouse?',
			body='DPI stages, lighting, polling rate, sleep timers and button mapping go back to factory defaults.',
		)
		dialog.add_response('cancel', 'Cancel')
		dialog.add_response('reset', 'Reset')
		dialog.set_response_appearance('reset', Adw.ResponseAppearance.DESTRUCTIVE)
		dialog.set_default_response('cancel')
		dialog.connect('response', lambda _d, r: r == 'reset' and self._reset())
		dialog.present(self.win)

	def _reset(self):
		def done(_r, error):
			if error:
				self.win.toast(f'Reset failed: {error}')
				return
			self.win.toast('Mouse reset to defaults')
			self.win.reload_all()

		self.win.worker.submit(service.reset, done)


# --- Buttons page -------------------------------------------------------------

class KeyCaptureDialog(Adw.AlertDialog):
	"""Asks the user to press a key combination and returns it as (modifiers, usage)."""

	def __init__(self, on_captured):
		super().__init__(heading='Press a key combination',
			body='It is stored on the mouse and sent whenever you press that button.')
		self.on_captured = on_captured
		self.add_response('cancel', 'Cancel')
		self.set_default_response('cancel')
		keys = Gtk.EventControllerKey()
		keys.connect('key-pressed', self._on_key)
		self.add_controller(keys)

	def _on_key(self, _c, keyval, _code, state):
		self._capture_key(keyval, state)
		return True  # the dialog swallows every key while it is open

	def _capture_key(self, keyval, state):
		name = Gdk.keyval_name(keyval) or ''
		if name in MODIFIER_BITS:  # wait for a real key
			return
		usage = KEY_TO_USAGE.get(name) or KEY_TO_USAGE.get(name.lower())
		if usage is None:
			return
		modifiers = 0
		if state & Gdk.ModifierType.CONTROL_MASK:
			modifiers |= 0x01
		if state & Gdk.ModifierType.SHIFT_MASK:
			modifiers |= 0x02
		if state & Gdk.ModifierType.ALT_MASK:
			modifiers |= 0x04
		if state & Gdk.ModifierType.SUPER_MASK:
			modifiers |= 0x08
		self.on_captured(modifiers, usage)
		self.close()


class MacroDialog(Adw.Window):
	"""Record a key/click sequence and write it to one button."""

	def __init__(self, win, button, existing, on_saved):
		super().__init__(transient_for=win, modal=True, title='Macro editor',
			default_width=560, default_height=620)
		self.win, self.button, self.on_saved = win, button, on_saved
		self.events = [dict(e) for e in (existing or {}).get('events', [])]
		self.recording = False
		self._last_time = None

		root = box(spacing=14, css=['page'])
		self.set_content(root)
		name = dict(PHYSICAL_BUTTONS).get(button, f'Button {button}')
		root.append(label(f'Macro for {name}', ['page-title']))
		root.append(label('Record keys, or add clicks by hand. Each step waits before the next one; '
			'the mouse always uses 10 ms after the final step.', ['muted', 'small'], wrap=True))

		tools = box(Gtk.Orientation.HORIZONTAL, 8)
		self.record_btn = Gtk.ToggleButton(label='Record keys')
		self.record_btn.add_css_class('ghost')
		self.record_btn.connect('toggled', self._toggle_record)
		tools.append(self.record_btn)
		click_menu = Gtk.DropDown.new_from_strings([m[1] for m in MOUSE_MACRO_CODES])
		tools.append(click_menu)
		tools.append(button_widget('Add click', ['ghost'], lambda: self._add_click(click_menu.get_selected())))
		tools.append(button_widget('Clear', ['ghost'], self._clear))
		root.append(tools)

		scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
		self.list_box = box(spacing=6)
		scroller.set_child(self.list_box)
		root.append(scroller)

		play = box(spacing=10, css=['card'])
		mode_row = box(Gtk.Orientation.HORIZONTAL, 12)
		mode_row.append(label('Play', ['muted'], width_chars=8))
		self.mode = Gtk.DropDown.new_from_strings(PLAY_MODES)
		self.mode.set_selected((existing or {}).get('mode', 0))
		self.mode.set_hexpand(True)
		self.mode.connect(NOTIFY_SELECTED, lambda *_: self._update_repeat())
		mode_row.append(self.mode)
		play.append(mode_row)
		repeat_row = box(Gtk.Orientation.HORIZONTAL, 12)
		repeat_row.append(label('Times', ['muted'], width_chars=8))
		self.repeat = Gtk.SpinButton.new_with_range(1, 255, 1)
		self.repeat.set_value((existing or {}).get('repeat', 1))
		repeat_row.append(self.repeat)
		play.append(repeat_row)
		root.append(play)
		self._update_repeat()

		actions = box(Gtk.Orientation.HORIZONTAL, 10, halign=Gtk.Align.END)
		actions.append(button_widget('Cancel', ['ghost'], self.close))
		actions.append(button_widget('Save to mouse', ['apply'], self._save))
		root.append(actions)

		keys = Gtk.EventControllerKey()
		keys.connect('key-pressed', self._on_key, False)
		keys.connect('key-released', self._on_key, True)
		self.add_controller(keys)
		self._refresh()

	def _update_repeat(self):
		self.repeat.set_sensitive(self.mode.get_selected() == 0)

	def _toggle_record(self, btn):
		self.recording = btn.get_active()
		btn.set_label('Stop recording' if self.recording else 'Record keys')
		self._last_time = None

	def _on_key(self, _c, keyval, _code, _state, released):
		if not self.recording:
			return False
		name = Gdk.keyval_name(keyval) or ''
		usage = KEY_TO_USAGE.get(name) or KEY_TO_USAGE.get(name.lower())
		if usage is None:
			return True
		now = time.monotonic()
		if self.events and self._last_time is not None:
			gap = int(round((now - self._last_time) * 1000))
			self.events[-1]['delayMs'] = max(10, min(gap, 50000))
		self._last_time = now
		self.events.append({'code': usage, 'release': released, 'delayMs': 10})
		self._refresh()
		return True

	def _add_click(self, index):
		code = MOUSE_MACRO_CODES[index][0]
		self.events.append({'code': code, 'release': False, 'delayMs': 10})
		self.events.append({'code': code, 'release': True, 'delayMs': 10})
		self._refresh()

	def _clear(self):
		self.events = []
		self._refresh()

	def _describe(self, event):
		code = event['code']
		name = dict(MOUSE_MACRO_CODES).get(code)
		if name is None:
			key = USAGE_TO_KEY.get(code, f'0x{code:02x}')
			name = key.replace('_', ' ') if len(key) > 1 else key.upper()
		return ('release ' if event.get('release') else 'press ') + name

	def _refresh(self):
		while (child := self.list_box.get_first_child()) is not None:
			self.list_box.remove(child)
		if not self.events:
			self.list_box.append(label('No steps yet.', ['muted']))
		for index, event in enumerate(self.events):
			row = box(Gtk.Orientation.HORIZONTAL, 10, ['event-row'])
			row.append(label(f'{index + 1}.', ['muted', 'mono'], width_chars=3))
			row.append(label(self._describe(event), hexpand=True))
			is_last = index == len(self.events) - 1
			spin = Gtk.SpinButton.new_with_range(10, 50000, 10)
			spin.set_value(10 if is_last else event.get('delayMs', 10))
			spin.set_sensitive(not is_last)
			spin.set_tooltip_text('The mouse forces 10 ms after the last step' if is_last else 'Wait after this step')
			spin.connect('value-changed', lambda w, i=index: self.events[i].update(delayMs=int(w.get_value())))
			row.append(spin)
			row.append(label('ms', ['muted'], width_chars=2))
			remove = Gtk.Button(icon_name='list-remove-symbolic', tooltip_text='Remove step')
			remove.connect('clicked', lambda _w, i=index: (self.events.pop(i), self._refresh()))
			row.append(remove)
			self.list_box.append(row)

	def _save(self):
		if not self.events:
			self.win.toast('Add at least one step first')
			return
		events = [{'code': e['code'], 'release': bool(e.get('release')), 'delayMs': int(e.get('delayMs', 10))}
			for e in self.events]
		macro = {'events': events, 'mode': self.mode.get_selected(), 'repeat': int(self.repeat.get_value())}

		def done(result, error):
			if error:
				self.win.toast(f'Couldn\u2019t save macro: {error}')
				return
			store.save_macro(self.button, macro)
			self.win.toast('Macro saved to the mouse')
			self.on_saved(result)
			self.close()

		self.win.worker.submit(
			lambda: service.set_macro(self.button, macro['events'], macro['mode'], macro['repeat']), done)


class ButtonRow(Gtk.Box):
	def __init__(self, page, slot, name):
		super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
		self.add_css_class('card')
		self.page, self.slot = page, slot
		self.value = [DISABLED, 0, 0]

		self.append(label(name, width_chars=16, valign=Gtk.Align.CENTER))
		self.actions = [a for a in ACTIONS if a[0] != MACRO_ACTION or slot in MACRO_CAPABLE]
		self.dropdown = Gtk.DropDown.new_from_strings([a[1] for a in self.actions])
		self.dropdown.set_hexpand(True)
		self.dropdown.set_valign(Gtk.Align.CENTER)
		self.dropdown.connect(NOTIFY_SELECTED, self._on_action)
		self.append(self.dropdown)
		self.detail = button_widget('', ['ghost'], self._edit_detail)
		self.detail.set_valign(Gtk.Align.CENTER)
		self.append(self.detail)
		self._loading = False
		self.changed = False

	def load(self, value):
		self._loading = True
		self.changed = False
		self.value = list(value)
		index = next((i for i, a in enumerate(self.actions) if a[0] == self.value[0]), None)
		if index is None:
			self.actions = self.actions + [(self.value[0], describe_slot(self.value))]
			self.dropdown.set_model(Gtk.StringList.new([a[1] for a in self.actions]))
			index = len(self.actions) - 1
		self.dropdown.set_selected(index)
		self._loading = False
		self._refresh_detail()

	def _on_action(self, *_):
		if self._loading:
			return
		action = self.actions[self.dropdown.get_selected()][0]
		if action == KEYBOARD_ACTION:
			self.value = [action, self.value[1], self.value[2]]
			if not self.value[2]:
				self._edit_detail()
		elif action == MACRO_ACTION:
			self.value = [action, 0, self.slot]
			self._edit_detail()
		else:
			self.value = [action, 0, ACTION_PARAMS.get(action, 0)]
		self.changed = True
		self._refresh_detail()
		self.page.mark_dirty()

	def _refresh_detail(self):
		action = self.value[0]
		if action == KEYBOARD_ACTION:
			self.detail.set_visible(True)
			self.detail.set_label(describe_slot(self.value) if self.value[2] else 'Set key…')
		elif action == MACRO_ACTION:
			macro = store.load_macros().get(str(self.slot))
			steps = len(macro['events']) if macro else 0
			self.detail.set_visible(True)
			self.detail.set_label(f'Edit macro ({steps} steps)' if steps else 'Record macro…')
		else:
			self.detail.set_visible(False)

	def _edit_detail(self):
		if self.value[0] == KEYBOARD_ACTION:
			def captured(modifiers, usage):
				self.value = [KEYBOARD_ACTION, modifiers, usage]
				self.changed = True
				self._refresh_detail()
				self.page.mark_dirty()

			KeyCaptureDialog(captured).present(self.page.win)
		elif self.value[0] == MACRO_ACTION:
			existing = store.load_macros().get(str(self.slot))
			MacroDialog(self.page.win, self.slot, existing, self.page.on_macro_saved).present()


class ButtonsPage(Gtk.Box):
	loaded = False  # nothing can be applied until the mouse has been read

	def __init__(self, win):
		super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
		self.add_css_class('page')
		self.win = win
		self._loading = False

		header, self.apply_btn = page_header('Buttons', 'What each button does', self.load, self.apply)
		self.append(header)
		self.rows = {}
		for slot, name in PHYSICAL_BUTTONS:
			row = ButtonRow(self, slot, name)
			self.rows[slot] = row
			self.append(row)
		self.append(label('Macros can go on the left, right, middle and the two side buttons. '
			'The mouse cannot read macros back, so this app keeps its own copy of them.',
			['muted', 'small'], wrap=True))
		self.apply_btn.set_sensitive(False)

	def mark_dirty(self):
		if not self._loading and self.loaded:
			self.apply_btn.set_sensitive(True)

	def on_macro_saved(self, slots):
		if slots:
			self._apply_slots(slots)

	def load(self):
		self.win.worker.submit(service.get_buttons, self._on_loaded)

	def _on_loaded(self, slots, error):
		if error:
			self.win.load_failed(self, 'button mapping', error)
			return
		self.loaded = True
		self.win.unread.discard(self)
		self._apply_slots(slots)

	def _apply_slots(self, slots):
		self._loading = True
		for slot, row in self.rows.items():
			row.load(slots.get(slot) or [DISABLED, 0, 0])
		self._loading = False
		self.apply_btn.set_sensitive(False)

	def apply(self):
		# Only rows the user touched: a button left on something this app does not
		# recognise (set by the Windows software, say) should not block the rest.
		values = {slot: row.value for slot, row in self.rows.items() if row.changed}
		if not values:
			self.win.toast('Nothing changed yet')
			self.apply_btn.set_sensitive(False)
			return
		current = {slot: row.value for slot, row in self.rows.items()}
		if not any(v[0] == 0x02 for v in current.values()):
			self.win.toast('Keep one button set to Left click, or you cannot click anything')
			return
		missing_key = [s for s, v in values.items() if v[0] == KEYBOARD_ACTION and not v[2]]
		if missing_key:
			self.win.toast('Pick a key for every button set to Keyboard key')
			return
		def done(slots, error):
			if error:
				self.win.toast(f'Couldn\u2019t apply buttons: {error}')
				return
			self._apply_slots(slots)
			self.win.toast('Button mapping applied')

		self.apply_btn.set_sensitive(False)
		self.win.worker.submit(lambda: service.set_buttons(values), done)


# --- Profiles page ------------------------------------------------------------

def _apply_profile(data):
	# Check up front: failing at the macro step would leave the profile half applied.
	if data.get('macros') and service.on_cable():
		raise device.WiredUnsupported('This profile has macros, which can only be written over '
		                              'the 2.4 GHz receiver. Nothing was changed.')
	dpi = data.get('dpi') or {}
	if dpi:
		service.set_performance(
			dpi['values'], dpi['colors'], dpi['current_stage'], dpi['active_stages'],
			dpi.get('angle_snap', False), dpi.get('ripple_control', True),
			data.get('polling'))
	if data.get('lighting'):
		service.set_lighting(**data['lighting'])
	for slot, macro in (data.get('macros') or {}).items():
		service.set_macro(int(slot), macro['events'], macro.get('mode', 0), macro.get('repeat', 1))
		store.save_macro(int(slot), macro)
	if data.get('buttons'):
		service.set_buttons({int(k): v for k, v in data['buttons'].items()})
	return True


class ProfilesPage(Gtk.Box):
	"""Saved snapshots of every setting, kept as files in ~/.config/attack-shark-x11."""

	def __init__(self, win):
		super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
		self.add_css_class('page')
		self.win = win

		header = box(Gtk.Orientation.HORIZONTAL, 12)
		titles = box(spacing=4, hexpand=True)
		titles.append(label('Profiles', ['page-title']))
		titles.append(label('Save every setting as a preset you can re-apply later', ['muted'], wrap=True))
		header.append(titles)
		save_btn = button_widget('Save current…', ['apply'], self.save_current)
		save_btn.set_valign(Gtk.Align.CENTER)
		header.append(save_btn)
		self.append(header)

		self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
		self.list_box.add_css_class('plain')
		self.append(self.list_box)
		self.empty = label('No profiles saved yet.', ['muted'])
		self.append(self.empty)

		actions = box(Gtk.Orientation.HORIZONTAL, 10)
		for text, handler in (('Apply to mouse', self.apply_selected), ('Rename…', self.rename_selected),
				('Export…', self.export_selected), ('Import…', self.import_profile)):
			actions.append(button_widget(text, ['ghost'], handler))
		delete = button_widget('Delete', ['danger'], self.delete_selected)
		actions.append(delete)
		self.append(actions)
		self.refresh()

	def refresh(self):
		while (child := self.list_box.get_first_child()) is not None:
			self.list_box.remove(child)
		names = store.list_profiles()
		for name in names:
			row = Gtk.ListBoxRow()
			row.set_child(label(name))
			row.profile_name = name
			self.list_box.append(row)
		self.empty.set_visible(not names)
		self.list_box.set_visible(bool(names))

	def _selected(self):
		row = self.list_box.get_selected_row()
		if row is None:
			self.win.toast('Pick a profile first')
			return None
		return row.profile_name

	def _confirm_overwrite(self, name, then):
		if not store.profile_exists(name):
			then(name)
			return
		dialog = Adw.AlertDialog(heading=f'Replace “{name}”?',
			body='A profile with that name already exists. Replacing it cannot be undone.')
		dialog.add_response('cancel', 'Cancel')
		dialog.add_response('replace', 'Replace')
		dialog.set_response_appearance('replace', Adw.ResponseAppearance.DESTRUCTIVE)
		dialog.set_default_response('cancel')
		dialog.connect('response', lambda _d, r: r == 'replace' and then(name))
		dialog.present(self.win)

	def _ask_name(self, heading, initial, on_name):
		dialog = Adw.AlertDialog(heading=heading)
		entry = Gtk.Entry(text=initial)
		dialog.set_extra_child(entry)
		dialog.add_response('cancel', 'Cancel')
		dialog.add_response('ok', 'Save')
		dialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
		dialog.set_default_response('ok')
		dialog.connect('response', lambda _d, r: r == 'ok' and entry.get_text().strip() and on_name(entry.get_text().strip()))
		dialog.present(self.win)

	def save_current(self):
		self._ask_name('Name this profile', f'Profile {len(store.list_profiles()) + 1}',
			lambda name: self._confirm_overwrite(name, self._save_as))

	def _save_as(self, name):
		def work():
			dpi, rate = service.get_performance()
			return {
				'name': name,
				'version': 1,
				'dpi': dpi,
				'polling': rate,
				'lighting': service.get_lighting(),
				'buttons': {str(k): v for k, v in service.get_buttons().items()},
				'macros': store.load_macros(),
			}

		def done(data, error):
			if error:
				self.win.toast(f'Couldn\u2019t read settings: {error}')
				return
			store.save_profile(name, data)
			self.refresh()
			self.win.toast(f'Saved “{name}”')

		self.win.worker.submit(work, done)

	def apply_selected(self):
		name = self._selected()
		if not name:
			return
		data = store.load_profile(name)
		if not data:
			self.win.toast('That profile could not be read')
			return

		def work():
			return _apply_profile(data)

		def done(_result, error):
			if error:
				self.win.toast(f'Couldn\u2019t apply profile: {error}')
				return
			self.win.toast(f'Applied “{name}”')
			self.win.reload_all()

		self.win.worker.submit(work, done)

	def rename_selected(self):
		name = self._selected()
		if name:
			def do_rename(new):
				if new == name:
					return
				self._confirm_overwrite(new, lambda target: (store.rename_profile(name, target), self.refresh()))

			self._ask_name('New name', name, do_rename)

	def delete_selected(self):
		name = self._selected()
		if not name:
			return
		dialog = Adw.AlertDialog(heading=f'Delete “{name}”?', body='The profile file is removed from disk.')
		dialog.add_response('cancel', 'Cancel')
		dialog.add_response('delete', 'Delete')
		dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
		dialog.connect('response', lambda _d, r: r == 'delete' and (store.delete_profile(name), self.refresh()))
		dialog.present(self.win)

	def export_selected(self):
		name = self._selected()
		if not name:
			return
		dialog = Gtk.FileDialog(initial_name=f'{name}.json')

		def done(d, result):
			try:
				file = d.save_finish(result)
			except GLib.Error:
				return
			try:
				store.export_profile(name, file.get_path())
				self.win.toast(f'Exported to {file.get_path()}')
			except OSError as e:
				self.win.toast(f'Export failed: {e}')

		dialog.save(self.win, None, done)

	def import_profile(self):
		dialog = Gtk.FileDialog()

		def done(d, result):
			try:
				file = d.open_finish(result)
			except GLib.Error:
				return
			try:
				name = store.import_profile(file.get_path())
				self.refresh()
				self.win.toast(f'Imported “{name}”')
			except (OSError, ValueError) as e:
				self.win.toast(f'Import failed: {e}')

		dialog.open(self.win, None, done)


# --- Pointer page ---------------------------------------------------------------

class PointerPage(Gtk.Box):
	"""Pointer speed and scrolling. These belong to Hyprland, not to the mouse."""

	def __init__(self, win):
		super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
		self.add_css_class('page')
		self.win = win
		self._loading = False

		header, self.apply_btn = page_header('Pointer', 'Speed, acceleration and scrolling', self.load, self.apply)
		self.append(header)

		if not hypr.available():
			self.append(label('Hyprland is not running, so these settings cannot be applied here.', ['muted'], wrap=True))
			self.apply_btn.set_sensitive(False)
			self.device = None
			return

		card = box(spacing=16, css=['card'])
		device_row = box(Gtk.Orientation.HORIZONTAL, 12)
		device_row.append(label('Device', ['muted'], width_chars=16))
		self.devices = hypr.mouse_devices()
		self.device_drop = Gtk.DropDown.new_from_strings(self.devices or ['none found'])
		self.device_drop.set_hexpand(True)
		device_row.append(self.device_drop)
		card.append(device_row)

		self.sensitivity = self._scale(card, 'Pointer speed', -1.0, 1.0, 0.05, 2)
		accel_row = box(Gtk.Orientation.HORIZONTAL, 12)
		accel_row.append(label('Acceleration', ['muted'], width_chars=16))
		self.accel = Gtk.DropDown.new_from_strings(['Hyprland default', 'Flat (no acceleration)', 'Adaptive'])
		self.accel.set_hexpand(True)
		self.accel.connect(NOTIFY_SELECTED, lambda *_: self._dirty())
		accel_row.append(self.accel)
		card.append(accel_row)
		self.scroll = self._scale(card, 'Scroll speed', 0.1, 3.0, 0.05, 2)
		self.natural = switch_row(card, 'Natural scrolling', 'Content follows your finger, like on a phone.', self._dirty)
		self.left_handed = switch_row(card, 'Left-handed', 'Swaps the left and right buttons.', self._dirty)
		self.append(card)

		persist = box(spacing=10, css=['card'])
		persist.append(label('Keep after restart', ['section-title']))
		self.persist_hint = label('', ['muted', 'small'], wrap=True)
		persist.append(self.persist_hint)
		row = box(Gtk.Orientation.HORIZONTAL, 10)
		row.append(button_widget('Write config file', ['ghost'], self.persist))
		row.append(button_widget('Copy source line', ['ghost'], self.copy_source))
		persist.append(row)
		self.append(persist)

		self.load()

	def _scale(self, parent, title, lo, hi, step, digits):
		row = box(Gtk.Orientation.HORIZONTAL, 12)
		row.append(label(title, ['muted'], width_chars=16))
		scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
		scale.set_hexpand(True)
		scale.set_digits(digits)
		scale.set_draw_value(True)
		scale.set_value_pos(Gtk.PositionType.RIGHT)
		scale.connect('value-changed', lambda _w: self._dirty())
		row.append(scale)
		parent.append(row)
		return scale

	def _dirty(self):
		if not self._loading:
			self.apply_btn.set_sensitive(True)

	def _settings(self):
		return {
			'sensitivity': round(self.sensitivity.get_value(), 2),
			'accel_profile': hypr.ACCEL_PROFILES[self.accel.get_selected()],
			'scroll_factor': round(self.scroll.get_value(), 2),
			'natural_scroll': self.natural.get_active(),
			'left_handed': self.left_handed.get_active(),
		}

	def _device(self):
		return self.devices[self.device_drop.get_selected()] if self.devices else None

	def load(self):
		if not getattr(self, 'devices', None):
			return
		self._loading = True
		saved = store.load_pointer()
		settings = {**hypr.global_settings(), **saved.get('settings', {})}
		if saved.get('device') in self.devices:
			self.device_drop.set_selected(self.devices.index(saved['device']))
		self.sensitivity.set_value(settings['sensitivity'])
		self.accel.set_selected(hypr.ACCEL_PROFILES.index(settings['accel_profile'])
			if settings['accel_profile'] in hypr.ACCEL_PROFILES else 0)
		self.scroll.set_value(settings['scroll_factor'])
		self.natural.set_active(bool(settings['natural_scroll']))
		self.left_handed.set_active(bool(settings['left_handed']))
		self._loading = False
		self.apply_btn.set_sensitive(False)
		self._update_hint()

	def _update_hint(self):
		if hypr.is_sourced():
			self.persist_hint.set_text('Your Hyprland config already loads this app\u2019s file, so saved settings '
				'come back after a restart.')
		else:
			self.persist_hint.set_text('Applying lasts until Hyprland restarts. To keep it, write the config file '
				f'and add this line to your Hyprland config:\n{hypr.SOURCE_LINE}')

	def apply(self):
		device_name = self._device()
		if not device_name:
			self.win.toast('No pointer device found')
			return
		try:
			hypr.apply(device_name, self._settings())
		except (RuntimeError, ValueError) as e:
			self.win.toast(str(e))
			return
		store.save_pointer({'device': device_name, 'settings': self._settings()})
		self.apply_btn.set_sensitive(False)
		self.win.toast('Pointer settings applied')

	def persist(self):
		device_name = self._device()
		if not device_name:
			return
		try:
			path = hypr.write_conf(device_name, self._settings())
		except (OSError, ValueError) as e:
			self.win.toast(str(e))
			return
		store.save_pointer({'device': device_name, 'settings': self._settings()})
		self._update_hint()
		self.win.toast(f'Wrote {path}')

	def copy_source(self):
		Gdk.Display.get_default().get_clipboard().set(hypr.SOURCE_LINE)
		self.win.toast('Source line copied')


# --- Window ------------------------------------------------------------------------

class MainWindow(Adw.ApplicationWindow):
	PAGES = ['performance', 'buttons', 'lighting', 'power', 'pointer', 'profiles']

	def __init__(self, app, start_page='performance'):
		self.start_page = start_page if start_page in self.PAGES else 'performance'
		super().__init__(application=app, title='Attack Shark X11', default_width=1180, default_height=760)
		self.worker = DeviceWorker(self._on_busy_changed)
		self.connect('close-request', self._on_close_request)

		self.toasts = Adw.ToastOverlay()
		root = box(Gtk.Orientation.HORIZONTAL, css=['app-bg'])
		self.toasts.set_child(root)
		self.set_content(self.toasts)

		self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True)
		self.lighting_page = LightingPage(self)
		self.power_page = PowerPage(self)
		self.perf_page = PerformancePage(self)
		self.buttons_page = ButtonsPage(self)
		self.profiles_page = ProfilesPage(self)
		self.pointer_page = PointerPage(self)
		for page, name in ((self.perf_page, 'performance'), (self.buttons_page, 'buttons'),
				(self.lighting_page, 'lighting'), (self.power_page, 'power'),
				(self.pointer_page, 'pointer'), (self.profiles_page, 'profiles')):
			scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=page)
			self.stack.add_named(scroller, name)

		root.append(self._build_sidebar())
		root.append(self.stack)

		self.unread = set()
		self.mouse_away = False  # asleep or not found: retries wait until it's back
		self._retry_id = 0
		self.reload_all()
		self.battery_stop = threading.Event()
		threading.Thread(target=service.watch_battery, daemon=True, args=(
			lambda *r: GLib.idle_add(self._show_battery, *r),
			lambda message: GLib.idle_add(self._show_missing, message),
			lambda: GLib.idle_add(self._show_asleep),
			self.battery_stop)).start()

	def _build_sidebar(self):
		side = box(spacing=28, css=['sidebar'], width_request=232)
		# Labels in the battery card expand within the card; without this GTK passes
		# that up and the sidebar takes a share of every extra pixel of window width.
		side.set_hexpand(False)

		brand = box(Gtk.Orientation.HORIZONTAL, 10, halign=Gtk.Align.CENTER)
		logo_path = os.path.join(APP_DIR, 'x11.png')
		if os.path.exists(logo_path):
			_, png = trimmed_pixbuf(logo_path, 40).save_to_bufferv('png', [], [])
			logo = Gtk.Picture.new_for_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)))
			logo.set_can_shrink(False)
		else:
			logo = Gtk.Image.new_from_icon_name('input-mouse-symbolic')
			logo.set_pixel_size(26)
		brand.append(logo)
		brand.append(label('Attack Shark X11', ['brand-title'], valign=Gtk.Align.CENTER))
		side.append(brand)

		nav = box(spacing=4)
		self.nav_buttons = {}
		for key, text, icon_name in (
			('performance', 'Performance', 'power-profile-performance-symbolic'),
			('buttons', 'Buttons', 'input-keyboard-symbolic'),
			('lighting', 'Lighting', 'display-brightness-symbolic'),
			('power', 'Power & device', 'battery-full-symbolic'),
			('pointer', 'Pointer', 'input-touchpad-symbolic'),
			('profiles', 'Profiles', 'view-list-symbolic'),
		):
			b = Gtk.Button()
			b.add_css_class('nav')
			content = box(Gtk.Orientation.HORIZONTAL, 12)
			content.append(Gtk.Image.new_from_icon_name(icon_name))
			content.append(label(text))
			b.set_child(content)
			b.connect('clicked', lambda _w, k=key: self.show_page(k))
			nav.append(b)
			self.nav_buttons[key] = b
		side.append(nav)

		spacer = box(vexpand=True)
		side.append(spacer)

		card = box(spacing=10, css=['card'])
		self.battery_card = card
		top = box(Gtk.Orientation.HORIZONTAL)
		top.append(label('Battery', ['muted', 'small'], hexpand=True))
		self.conn_label = label('● Connecting', ['muted', 'small'])
		top.append(self.conn_label)
		card.append(top)
		headline = box(Gtk.Orientation.HORIZONTAL, 8)
		self.battery_icon = Gtk.Image(icon_name='battery-missing-symbolic', pixel_size=24)
		headline.append(self.battery_icon)
		self.battery_label = label('—', ['big-number'])
		headline.append(self.battery_label)
		card.append(headline)
		self.battery_bar = Gtk.LevelBar(min_value=0, max_value=100)
		self.battery_bar.add_css_class('battery')
		self.battery_bar.add_offset_value('low', 20)
		self.battery_bar.add_offset_value('filled', 100)
		card.append(self.battery_bar)
		state_row = box(Gtk.Orientation.HORIZONTAL, 8)
		self.battery_state = label('', ['muted', 'small'], hexpand=True, wrap=True, max_width_chars=24)
		state_row.append(self.battery_state)
		card.append(state_row)
		self.battery_note = label('', ['muted', 'small'], wrap=True, max_width_chars=24)
		card.append(self.battery_note)
		side.append(card)

		self.show_page(self.start_page)
		return side

	def show_page(self, key):
		self.stack.set_visible_child_name(key)
		for k, b in self.nav_buttons.items():
			(b.add_css_class if k == key else b.remove_css_class)('active')

	def toast(self, text):
		self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

	def reload_all(self):
		self.perf_page.load()
		self.buttons_page.load()
		self.lighting_page.load()

	def load_failed(self, page, what, error):
		# Usually the mouse was asleep or out of range. Its pages stay locked until
		# a read works, so placeholder values can never be applied over real ones.
		if page not in self.unread:
			self.toast(f'Couldn’t read {what} yet ({error}). Retrying…')
		self.unread.add(page)
		if not self._retry_id:
			self._retry_id = GLib.timeout_add_seconds(3, lambda: bool(self._retry_loads()))

	def _retry_loads(self):
		self._retry_id = 0
		if self.mouse_away:
			return  # _show_battery retries as soon as it's back
		for page in self.unread.copy():
			page.load()  # a page leaves self.unread once its read succeeds

	def _set_conn(self, text, css):
		for c in ('ok', 'err', 'muted'):
			self.conn_label.remove_css_class(c)
		self.conn_label.set_text(text)
		self.conn_label.add_css_class(css)

	def _show_missing(self, message):
		self.mouse_away = True
		self._set_conn('● Not found', 'err')
		# Clear the reading: an old percentage next to "Not found" looks current.
		self.battery_icon.set_from_icon_name('battery-missing-symbolic')
		for widget in (self.battery_card, self.battery_bar):
			widget.remove_css_class('charging')
		for css in ('big-word', 'charge', 'ok'):
			self.battery_label.remove_css_class(css)
		self.battery_label.add_css_class('big-number')
		self.battery_label.set_text('—')
		self.battery_bar.set_value(0)
		self.battery_state.set_text('')
		self.battery_note.set_text(message)
		return False

	def _show_asleep(self):
		self.mouse_away = True
		self._set_conn('● Asleep', 'muted')
		for widget in (self.battery_card, self.battery_bar):
			widget.remove_css_class('charging')
		self.battery_note.set_text('Last reading shown. Move the mouse to wake it.')
		return False

	def _show_battery(self, percent, status, estimate, settling, wired=False):
		if self.mouse_away:
			self.mouse_away = False
			if self.unread:
				self._retry_loads()
		charging = status == device.BATTERY_CHARGING
		full = status == device.BATTERY_FULL
		level = min(100, (percent + 5) // 10 * 10)
		for widget, css in ((self.battery_card, 'charging'), (self.battery_bar, 'charging'),
				(self.battery_icon, 'charge'), (self.battery_icon, 'ok'),
				(self.battery_label, 'big-number'), (self.battery_label, 'big-word'),
				(self.battery_label, 'charge'), (self.battery_label, 'ok')):
			widget.remove_css_class(css)

		# The percentage is a voltage reading that sits high on the dock, so while
		# charging the state is the headline and the number is only a footnote,
		# the same way the vendor's software hides it.
		if charging:
			self._show_charging(percent, level, wired)
		elif full:
			self._show_full(wired)
		else:
			self._show_discharging(percent, level, status)
		self.battery_bar.set_value(100 if full else percent)
		self.battery_note.set_text(self._battery_note(percent, charging or full, estimate, settling))
		return False

	def _show_charging(self, percent, level, wired):
		self.battery_icon.set_from_icon_name(f'battery-level-{min(level, 90)}-charging-symbolic')
		self.battery_label.set_text('Charging')
		for widget in (self.battery_card, self.battery_bar):
			widget.add_css_class('charging')
		for css in ('big-word', 'charge'):
			self.battery_label.add_css_class(css)
		self.battery_icon.add_css_class('charge')
		source = 'From this PC over USB' if wired else 'On the dock or a charger'
		self.battery_state.set_text(f'{source}. Mouse reports {percent}% (reads high while charging)')
		self._set_conn('● USB cable' if wired else '● Charging', 'ok')

	def _show_full(self, wired):
		self.battery_icon.set_from_icon_name('battery-level-100-charged-symbolic')
		self.battery_label.set_text('Full')
		for css in ('big-word', 'ok'):
			self.battery_label.add_css_class(css)
		self.battery_icon.add_css_class('ok')
		self.battery_state.set_text('Fully charged')
		self._set_conn('● USB cable' if wired else '● Charging', 'ok')

	def _show_discharging(self, percent, level, status):
		self.battery_icon.set_from_icon_name(f'battery-level-{level}-symbolic')
		self.battery_label.add_css_class('big-number')
		self.battery_label.set_markup(f'{percent}<span size="small" foreground="#9aa0ad">%</span>')
		self.battery_state.set_text(service.describe_battery_status(status))
		self._set_conn('● Connected', 'ok')

	@staticmethod
	def _battery_note(percent, plugged_in, estimate, settling):
		if settling:
			note = 'Reads high for a few minutes after the dock'
		elif plugged_in:
			note = ''
		elif percent >= 99:
			# Freshly charged, the reading sits at 100 for a while; with no drop
			# yet there's no drain rate to estimate from.
			note = 'Time left appears once the level starts to drop'
		else:
			note = 'Working out time left (about 20 minutes)'
		if estimate:
			return f'{estimate}. {note}' if settling else estimate
		return note

	def _on_busy_changed(self, busy):
		GLib.idle_add(lambda: self.set_cursor_from_name('progress' if busy else None) and False)

	def _on_close_request(self, _w):
		if self.worker.busy:
			self.toast('Still talking to the mouse — close again in a moment')
			return True
		self.battery_stop.set()
		return False


class App(Adw.Application):
	def __init__(self):
		super().__init__(application_id=APP_ID)

	def do_startup(self):
		Adw.Application.do_startup(self)
		Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
		provider = Gtk.CssProvider()
		provider.load_from_string(CSS)
		Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

	def do_activate(self):
		page = 'performance'
		argv = sys.argv
		if '--page' in argv and argv.index('--page') + 1 < len(argv):
			page = argv[argv.index('--page') + 1]
		win = self.props.active_window or MainWindow(self, page)
		win.present()

	def do_shutdown(self):
		Adw.Application.do_shutdown(self)


if __name__ == '__main__':
	App().run(None)
