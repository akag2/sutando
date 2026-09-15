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
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Plan } from '../src/platform-desktop.js';

import { classifyPlatform, hasDisplay, unsupportedPlatformMessage } from '../src/platform.js';
import {
	activateAppByTitlePlan,
	activateAppPlan,
	activateForKeystroke,
	activateOnlyPlan,
	clipboardReadPlan,
	clipboardWritePlan,
	collapseSelectionRightPlan,
	hasCommand,
	linuxKeySpec,
	linuxModifier,
	notifyPlan,
	openPathPlan,
	pastePlan,
	pressKeyPlan,
	resetCommandCacheForTests,
	runOpen,
	runPlan,
	selectAllPlan,
	typeTextPlan,
	volumePlan,
	xdotoolWindowPattern,
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
		// Hand-typed expectation (NOT built with appleScriptString): every `"` in
		// the hostile value arrives as `\"`, so osascript sees one string literal.
		assert.equal(plan.args[1], 'tell application "\\"; do shell script \\"rm -rf ~\\"; tell application \\"Finder" to activate');
		assert.ok(plan.args[1].includes('\\"; do shell script'));
		assert.ok(!plan.args[1].includes(' ""; do shell script'));
		assert.match(plan.args[3], /set frontmost of process ".*" to true$/);
	});
	it('linux: xdotool search by class, regex-escaped and anchored, first match activated', () => {
		const plan = activateAppPlan('chromium', 'chromium', 'linux');
		assert.equal(plan.cmd, 'xdotool');
		assert.deepEqual(plan.args, ['search', '--onlyvisible', '--class', '^chromium', 'windowactivate', '--sync', '%1']);
		// A name is a literal, never a regex or an option: `c++` and `-foo` are inert.
		assert.equal(xdotoolWindowPattern('c++'), '^c\\+\\+');
		assert.equal(xdotoolWindowPattern('-foo.bar'), '^-foo\\.bar');
		assert.ok(!activateAppByTitlePlan('-x').args.some(a => a === '-x'));
	});
	it('press_key activation is the single darwin `activate` line at 3s — never the frontmost set', () => {
		assert.deepEqual(activateOnlyPlan('Slack', 'darwin'), {
			cmd: 'osascript', args: ['-e', 'tell application "Slack" to activate'], timeout: 3_000,
		});
		assert.equal(activateOnlyPlan('slack', 'linux').timeout, 3_000);
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

describe('type_text paste chords keep the pre-port darwin argv and timeouts', () => {
	// Independent expected strings (typed by hand from the parent commit's
	// inline-tools.ts), NOT built with appleScriptString — so a change to the
	// escape helper cannot rewrite the expectation along with the plan.
	it('select-all / collapse-right / paste are the character-based chords, 3s/3s/5s', () => {
		assert.deepEqual(selectAllPlan('darwin'), { cmd: 'osascript', args: ['-e', 'tell application "System Events" to keystroke "a" using command down'], timeout: 3_000 });
		assert.deepEqual(collapseSelectionRightPlan('darwin'), { cmd: 'osascript', args: ['-e', 'tell application "System Events" to key code 124'], timeout: 3_000 });
		assert.deepEqual(pastePlan('darwin'), { cmd: 'osascript', args: ['-e', 'tell application "System Events" to keystroke "v" using command down'], timeout: 5_000 });
	});
	it('the paste branch clipboard calls keep their 2s bound; the clipboard tool its 5s', () => {
		assert.equal(clipboardReadPlan('darwin', NO_TOOLS, 2_000).timeout, 2_000);
		assert.equal(clipboardWritePlan('x', 'darwin', NO_TOOLS, 2_000).timeout, 2_000);
		assert.equal(clipboardReadPlan('darwin', NO_TOOLS).timeout, 5_000);
	});
	it('linux: ctrl+a / Right / ctrl+v', () => {
		assert.deepEqual(selectAllPlan('linux').args, ['key', '--clearmodifiers', 'ctrl+a']);
		assert.deepEqual(collapseSelectionRightPlan('linux').args, ['key', '--clearmodifiers', 'Right']);
		assert.deepEqual(pastePlan('linux').args, ['key', '--clearmodifiers', 'ctrl+v']);
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
	it('linux: xsel when xclip is absent; text still via stdin, never argv; writers are quiet', () => {
		const w = clipboardWritePlan('secret', 'linux', NO_TOOLS);
		assert.equal(w.cmd, 'xsel');
		assert.equal(w.input, 'secret');
		assert.ok(!w.args.includes('secret'));
		assert.equal(w.quietOutput, true);
		assert.equal(clipboardReadPlan('linux', NO_TOOLS).cmd, 'xsel');
		assert.equal(clipboardReadPlan('linux', NO_TOOLS).quietOutput, undefined, 'a reader must keep stdout');
	});
});

describe('runners', () => {
	it('a quiet writer returns as soon as the writer exits, even if a forked child keeps the pipes', () => {
		// xclip's shape: the parent exits 0 at once, a background child holds
		// stdout/stderr open. Without quietOutput this timed out at `timeout`.
		const t0 = Date.now();
		runPlan({ cmd: 'sh', args: ['-c', 'cat >/dev/null; sleep 2 & exit 0'], input: 'x', timeout: 1_000, quietOutput: true });
		assert.ok(Date.now() - t0 < 900, 'quiet writer waited on the daemon');
		assert.throws(
			() => runPlan({ cmd: 'sh', args: ['-c', 'sleep 2 & exit 0'], input: 'x', timeout: 150 }),
			/ETIMEDOUT|timed out/i,
			'control: the piped form still reproduces the stall',
		);
	});
	it('runOpen rejects on a missing opener instead of raising an unhandled error event', async () => {
		await assert.rejects(runOpen({ cmd: 'sutando-test-opener-that-does-not-exist', args: ['/tmp/x'] }), /ENOENT/);
		await runOpen({ cmd: 'sh', args: ['-c', 'exit 0'] }); // a real spawn resolves
	});
	it('hasCommand answers per PATH, and a miss is never remembered', () => {
		resetCommandCacheForTests();
		assert.equal(hasCommand('sh', { PATH: '/nonexistent' }), false);
		assert.equal(hasCommand('sh', { PATH: '/bin:/usr/bin' }), true);
		assert.equal(hasCommand('sh', { PATH: '/nonexistent' }), false);
		// Install a tool into a PATH dir that was already probed empty.
		const dir = mkdtempSync(join(tmpdir(), 'hascmd-'));
		assert.equal(hasCommand('sutando-later-tool', { PATH: dir }), false);
		writeFileSync(join(dir, 'sutando-later-tool'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
		assert.equal(hasCommand('sutando-later-tool', { PATH: dir }), true, 'a later install must be seen without a restart');
		rmSync(dir, { recursive: true, force: true });
	});
});

describe('press_key target activation (injected runner, no desktop)', () => {
	const calls: string[][] = [];
	const scene = (visible: string[]) => (plan: Plan) => {
		calls.push([plan.cmd, ...plan.args]);
		if (plan.cmd !== 'xdotool') return '';
		const pattern = plan.args[3]; // ^name
		const byTitle = plan.args.includes('--name');
		const hit = visible.some(w => new RegExp(pattern, 'i').test(byTitle ? `Review page - ${w}` : w));
		if (!hit) throw new Error('xdotool: no match');
		return '';
	};
	it('linux: "chrome" is aliased to the chromium class and activates it', () => {
		calls.length = 0;
		const r = activateForKeystroke('chrome', 'linux', scene(['chromium']));
		assert.deepEqual(r, { ok: true });
		assert.equal(calls[0][4], '^chromium');
	});
	it('linux: class miss falls back to title; both misses report an error, so the caller fires nothing', () => {
		calls.length = 0;
		const r = activateForKeystroke('chrome', 'linux', scene(['gedit']));
		assert.equal(r.ok, false);
		assert.deepEqual(calls.map(c => c.slice(0, 4)), [
			['xdotool', 'search', '--onlyvisible', '--class'],
			['xdotool', 'search', '--onlyvisible', '--name'],
		]);
		assert.match((r as { error: string }).error, /chromium/);
	});
	it('darwin: the single activate line, alias applied, failure reported but not retried', () => {
		calls.length = 0;
		const r = activateForKeystroke('chrome', 'darwin', (plan) => { calls.push([plan.cmd, ...plan.args]); throw new Error('denied'); });
		assert.equal(r.ok, false);
		assert.equal(calls.length, 1);
		assert.deepEqual(calls[0], ['osascript', '-e', 'tell application "Google Chrome" to activate']);
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
