/**
 * Contract tests for src/platform.ts + src/platform-desktop.ts — the Linux
 * port's TypeScript half.
 *
 * Two things are pinned. (1) The supported-platform gate: voice-agent.ts used
 * to exit on anything but darwin; linux is now admitted, anything else still
 * refused, and SUTANDO_TEST_MODE keeps its escape hatch. (2) The command
 * PLANS: for each inline tool the exact argv per platform — darwin must be the
 * osascript/open/pbcopy invocation the tools ran before (byte-for-byte for the
 * AppleScript, escaping included), linux the xdotool/xdg-open/xclip form. Plans
 * are pure, so this runs on any host without spawning a desktop tool.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { classifyPlatform, hasDisplay, unsupportedPlatformMessage } from '../src/platform.js';
import {
	activateAppPlan,
	appleScriptString,
	clipboardReadPlan,
	clipboardWritePlan,
	linuxKeySpec,
	linuxModifier,
	notifyPlan,
	openPathPlan,
	pressKeyPlan,
	typeTextPlan,
	volumePlan,
} from '../src/platform-desktop.js';

const NO_TOOLS = { PATH: '/nonexistent' } as NodeJS.ProcessEnv;

describe('platform gate', () => {
	it('classifies darwin, linux, and everything else', () => {
		assert.equal(classifyPlatform('darwin'), 'darwin');
		assert.equal(classifyPlatform('linux'), 'linux');
		assert.equal(classifyPlatform('win32'), 'unsupported');
		assert.equal(classifyPlatform('freebsd'), 'unsupported');
	});
	it('admits darwin and linux, refuses others with a message naming the platform', () => {
		assert.equal(unsupportedPlatformMessage('darwin', {}), null);
		assert.equal(unsupportedPlatformMessage('linux', {}), null);
		assert.match(unsupportedPlatformMessage('win32', {}) ?? '', /win32/);
	});
	it('SUTANDO_TEST_MODE=1 still bypasses the gate', () => {
		assert.equal(unsupportedPlatformMessage('win32', { SUTANDO_TEST_MODE: '1' }), null);
	});
	it('hasDisplay: darwin always, linux only with DISPLAY/WAYLAND_DISPLAY', () => {
		assert.equal(hasDisplay('darwin', {}), true);
		assert.equal(hasDisplay('linux', {}), false);
		assert.equal(hasDisplay('linux', { DISPLAY: ':99' }), true);
		assert.equal(hasDisplay('linux', { WAYLAND_DISPLAY: 'wayland-0' }), true);
		assert.equal(hasDisplay('win32', { DISPLAY: ':0' }), false);
	});
});

describe('open', () => {
	it('darwin: open [-a app] path (unchanged)', () => {
		assert.deepEqual(openPathPlan('/tmp/a.pdf', undefined, 'darwin'), { cmd: 'open', args: ['/tmp/a.pdf'], timeout: 5_000 });
		assert.deepEqual(openPathPlan('/tmp/a.pdf', 'Preview', 'darwin'), { cmd: 'open', args: ['-a', 'Preview', '/tmp/a.pdf'], timeout: 5_000 });
	});
	it('linux: xdg-open, or the named command', () => {
		assert.deepEqual(openPathPlan('/tmp/a.pdf', undefined, 'linux'), { cmd: 'xdg-open', args: ['/tmp/a.pdf'] });
		assert.deepEqual(openPathPlan('/tmp/a.pdf', 'evince', 'linux'), { cmd: 'evince', args: ['/tmp/a.pdf'] });
	});
});

describe('activate app', () => {
	it('darwin: the two-line AppleScript, with the injection escape intact', () => {
		const hostile = '"; do shell script "rm -rf ~"; tell application "Finder';
		const plan = activateAppPlan(hostile, hostile, 'darwin');
		assert.equal(plan.cmd, 'osascript');
		// Every embedded quote is escaped, so the literal cannot be broken out of:
		// the hostile `"; do shell script` reaches osascript as `\"; do shell script`.
		assert.equal(plan.args[1], `tell application "${appleScriptString(hostile)}" to activate`);
		assert.ok(plan.args[1].includes('\\"; do shell script'));
		assert.ok(!plan.args[1].includes(' ""; do shell script'));
		assert.match(plan.args[3], /set frontmost of process ".*" to true$/);
	});
	it('linux: xdotool search by class, activate the newest match', () => {
		const plan = activateAppPlan('chromium', 'chromium', 'linux');
		assert.equal(plan.cmd, 'xdotool');
		assert.deepEqual(plan.args, ['search', '--onlyvisible', '--class', 'chromium', 'windowactivate', '--sync', '%@']);
	});
});

describe('press key', () => {
	it('darwin: known words use key code, unknown use keystroke, modifiers appended', () => {
		assert.deepEqual(pressKeyPlan('enter', [], 'darwin').args, ['-e', 'tell application "System Events" to key code 36']);
		assert.deepEqual(pressKeyPlan('downarrow', [], 'darwin').args, ['-e', 'tell application "System Events" to key code 125']);
		assert.deepEqual(pressKeyPlan('v', ['command'], 'darwin').args, ['-e', 'tell application "System Events" to key code 9 using {command down}']);
		assert.deepEqual(pressKeyPlan('F11', ['shift', 'option'], 'darwin').args, ['-e', 'tell application "System Events" to keystroke "F11" using {shift down, option down}']);
		assert.deepEqual(pressKeyPlan('"', [], 'darwin').args, ['-e', 'tell application "System Events" to keystroke "\\""']);
	});
	it('linux: xdotool key with keysym names and ctrl for "command"', () => {
		assert.deepEqual(pressKeyPlan('enter', [], 'linux').args, ['key', '--clearmodifiers', 'Return']);
		assert.deepEqual(pressKeyPlan('downarrow', [], 'linux').args, ['key', '--clearmodifiers', 'Down']);
		assert.deepEqual(pressKeyPlan('v', ['command'], 'linux').args, ['key', '--clearmodifiers', 'ctrl+v']);
		assert.deepEqual(pressKeyPlan('a', ['command', 'shift'], 'linux').args, ['key', '--clearmodifiers', 'ctrl+shift+a']);
		assert.equal(linuxKeySpec('F11', ['option']), 'alt+F11');
		assert.equal(linuxModifier('control'), 'ctrl');
	});
});

describe('type text', () => {
	it('darwin: keystroke with AppleScript escaping', () => {
		assert.deepEqual(typeTextPlan('say "hi"', 'darwin').args, ['-e', 'tell application "System Events" to keystroke "say \\"hi\\""']);
	});
	it('linux: xdotool type, text after -- so a leading dash is typed', () => {
		assert.deepEqual(typeTextPlan('-v flag', 'linux').args, ['type', '--clearmodifiers', '--delay', '12', '--', '-v flag']);
	});
});

describe('clipboard', () => {
	it('darwin: pbpaste / pbcopy via stdin', () => {
		assert.deepEqual(clipboardReadPlan('darwin', NO_TOOLS), { cmd: 'pbpaste', args: [], timeout: 5_000 });
		assert.deepEqual(clipboardWritePlan('🤖', 'darwin', NO_TOOLS), { cmd: 'pbcopy', args: [], input: '🤖', timeout: 5_000 });
	});
	it('linux: xsel when xclip is absent; text still via stdin, never argv', () => {
		const w = clipboardWritePlan('secret', 'linux', NO_TOOLS);
		assert.equal(w.cmd, 'xsel');
		assert.equal(w.input, 'secret');
		assert.ok(!w.args.includes('secret'));
		assert.equal(clipboardReadPlan('linux', NO_TOOLS).cmd, 'xsel');
	});
});

describe('volume + notify', () => {
	it('darwin: the three osascript forms', () => {
		assert.deepEqual(volumePlan({ mute: true }, 'darwin', NO_TOOLS).args, ['-e', 'set volume with output muted']);
		assert.deepEqual(volumePlan({ mute: false }, 'darwin', NO_TOOLS).args, ['-e', 'set volume without output muted']);
		assert.deepEqual(volumePlan({ level: 42 }, 'darwin', NO_TOOLS).args, ['-e', 'set volume output volume 42']);
	});
	it('linux: amixer fallback when pactl is absent', () => {
		assert.deepEqual(volumePlan({ mute: true }, 'linux', NO_TOOLS).args, ['-q', 'sset', 'Master', 'mute']);
		assert.deepEqual(volumePlan({ level: 42 }, 'linux', NO_TOOLS).args, ['-q', 'sset', 'Master', '42%']);
	});
	it('notify: osascript vs notify-send', () => {
		assert.equal(notifyPlan('Sutando', 'x', 'darwin').cmd, 'osascript');
		assert.deepEqual(notifyPlan('Sutando', 'Captured', 'linux').args, ['Sutando', 'Captured']);
	});
});
