/**
 * Desktop-automation backend for the inline tools: open a file, activate an
 * app, press a key, type/paste text, read/write the clipboard, set volume.
 *
 * Every tool in inline-tools.ts used to spawn the macOS binary directly
 * (osascript / open / pbcopy / pbpaste). This module owns that choice per
 * platform: darwin keeps the exact commands it always ran; linux routes the
 * same operations through xdg-open, xdotool, xclip (or xsel) and pactl (or
 * amixer) against the session's $DISPLAY — a real desktop or an Xvfb virtual
 * one. The tools stay platform-blind.
 *
 * Split into PLANS and RUNNERS. A plan is a pure `{ cmd, args, input? }` for
 * one platform, so the exact argv is testable on any host; a runner executes
 * a plan with the same execFileSync shape (no shell, argv array) the tools
 * used before. Keystroke plans keep the tools' AppleScript escaping on darwin
 * and pass literal strings on linux, where no interpreter sits in between.
 */

import { execFileSync, spawn } from 'node:child_process';
import { classifyPlatform, type HostPlatform } from './platform.js';

export type Plan = { cmd: string; args: string[]; input?: string; timeout?: number };

export type Modifier = 'command' | 'shift' | 'control' | 'option';

/** Escape for an AppleScript string literal (darwin plans only). */
export function appleScriptString(s: string): string {
	return s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

// --- key naming --------------------------------------------------------------

/** macOS virtual key codes for the keys press_key names by word. */
const DARWIN_KEY_CODES: Record<string, number> = {
	enter: 36, return: 36, escape: 53, esc: 53, tab: 48,
	delete: 51, backspace: 51, space: 49,
	up: 126, down: 125, left: 123, right: 124,
	// Common voice-spoken aliases — without these, the fallthrough to
	// `keystroke "<key>"` types the literal string into the focused field.
	uparrow: 126, downarrow: 125, leftarrow: 123, rightarrow: 124,
	arrowup: 126, arrowdown: 125, arrowleft: 123, arrowright: 124,
	a: 0, c: 8, v: 9, x: 7, z: 6, f: 3, s: 1, w: 13, q: 12,
};

/** X keysym names for the same words (xdotool key). */
const LINUX_KEYSYMS: Record<string, string> = {
	enter: 'Return', return: 'Return', escape: 'Escape', esc: 'Escape', tab: 'Tab',
	delete: 'BackSpace', backspace: 'BackSpace', space: 'space',
	up: 'Up', down: 'Down', left: 'Left', right: 'Right',
	uparrow: 'Up', downarrow: 'Down', leftarrow: 'Left', rightarrow: 'Right',
	arrowup: 'Up', arrowdown: 'Down', arrowleft: 'Left', arrowright: 'Right',
	home: 'Home', end: 'End', pageup: 'Prior', pagedown: 'Next',
};

/**
 * Voice users say "command" for the primary shortcut key; on Linux that is
 * Ctrl (Cmd+C → Ctrl+C). Option is Alt. Control stays Ctrl.
 */
export function linuxModifier(m: Modifier): string {
	switch (m) {
		case 'command': return 'ctrl';
		case 'control': return 'ctrl';
		case 'option': return 'alt';
		case 'shift': return 'shift';
	}
}

/** xdotool key spec such as `ctrl+shift+Return`; single characters pass through. */
export function linuxKeySpec(key: string, modifiers: Modifier[] = []): string {
	const name = LINUX_KEYSYMS[key.toLowerCase()] ?? key;
	return [...modifiers.map(linuxModifier), name].join('+');
}

// --- plans -------------------------------------------------------------------

export function openPathPlan(path: string, app: string | undefined, platform: HostPlatform): Plan {
	if (platform === 'darwin') {
		return { cmd: 'open', args: app ? ['-a', app, path] : [path], timeout: 5_000 };
	}
	// An explicit app is a command on PATH; otherwise the desktop's handler.
	return app ? { cmd: app, args: [path] } : { cmd: 'xdg-open', args: [path] };
}

export function activateAppPlan(app: string, processName: string, platform: HostPlatform): Plan {
	if (platform === 'darwin') {
		return {
			cmd: 'osascript',
			args: [
				'-e', `tell application "${appleScriptString(app)}" to activate`,
				'-e', `tell application "System Events" to set frontmost of process "${appleScriptString(processName)}" to true`,
			],
			timeout: 10_000,
		};
	}
	// Match the WM_CLASS first (stable, e.g. "chromium"), most-recent window wins.
	return { cmd: 'xdotool', args: ['search', '--onlyvisible', '--class', app, 'windowactivate', '--sync', '%@'], timeout: 10_000 };
}

/** Second-chance activation by window title, for apps whose class differs from their name. */
export function activateAppByTitlePlan(app: string): Plan {
	return { cmd: 'xdotool', args: ['search', '--onlyvisible', '--name', app, 'windowactivate', '--sync', '%@'], timeout: 10_000 };
}

export function pressKeyPlan(key: string, modifiers: Modifier[], platform: HostPlatform): Plan {
	if (platform === 'darwin') {
		const modStr = modifiers.length ? ` using {${modifiers.map(m => m + ' down').join(', ')}}` : '';
		const code = DARWIN_KEY_CODES[key.toLowerCase()];
		const script = code === undefined
			? `tell application "System Events" to keystroke "${appleScriptString(key)}"${modStr}`
			: `tell application "System Events" to key code ${code}${modStr}`;
		return { cmd: 'osascript', args: ['-e', script], timeout: 3_000 };
	}
	return { cmd: 'xdotool', args: ['key', '--clearmodifiers', linuxKeySpec(key, modifiers)], timeout: 3_000 };
}

/** Type a short single-line string at the caret. */
export function typeTextPlan(text: string, platform: HostPlatform): Plan {
	if (platform === 'darwin') {
		return { cmd: 'osascript', args: ['-e', `tell application "System Events" to keystroke "${appleScriptString(text)}"`], timeout: 5_000 };
	}
	// `--` ends option parsing so text starting with `-` is typed, not parsed.
	return { cmd: 'xdotool', args: ['type', '--clearmodifiers', '--delay', '12', '--', text], timeout: 15_000 };
}

export function clipboardReadPlan(platform: HostPlatform, env: NodeJS.ProcessEnv = process.env): Plan {
	if (platform === 'darwin') return { cmd: 'pbpaste', args: [], timeout: 5_000 };
	return hasCommand('xclip', env)
		? { cmd: 'xclip', args: ['-o', '-selection', 'clipboard'], timeout: 5_000 }
		: { cmd: 'xsel', args: ['--output', '--clipboard'], timeout: 5_000 };
}

export function clipboardWritePlan(text: string, platform: HostPlatform, env: NodeJS.ProcessEnv = process.env): Plan {
	if (platform === 'darwin') return { cmd: 'pbcopy', args: [], input: text, timeout: 5_000 };
	return hasCommand('xclip', env)
		? { cmd: 'xclip', args: ['-i', '-selection', 'clipboard'], input: text, timeout: 5_000 }
		: { cmd: 'xsel', args: ['--input', '--clipboard'], input: text, timeout: 5_000 };
}

export function volumePlan(
	action: { mute: true } | { mute: false } | { level: number },
	platform: HostPlatform,
	env: NodeJS.ProcessEnv = process.env,
): Plan {
	if (platform === 'darwin') {
		const script = 'mute' in action
			? (action.mute ? 'set volume with output muted' : 'set volume without output muted')
			: `set volume output volume ${action.level}`;
		return { cmd: 'osascript', args: ['-e', script], timeout: 5_000 };
	}
	if (hasCommand('pactl', env)) {
		return 'mute' in action
			? { cmd: 'pactl', args: ['set-sink-mute', '@DEFAULT_SINK@', action.mute ? '1' : '0'], timeout: 5_000 }
			: { cmd: 'pactl', args: ['set-sink-volume', '@DEFAULT_SINK@', `${action.level}%`], timeout: 5_000 };
	}
	return 'mute' in action
		? { cmd: 'amixer', args: ['-q', 'sset', 'Master', action.mute ? 'mute' : 'unmute'], timeout: 5_000 }
		: { cmd: 'amixer', args: ['-q', 'sset', 'Master', `${action.level}%`], timeout: 5_000 };
}

/** Desktop notification (fire-and-forget by callers). */
export function notifyPlan(title: string, message: string, platform: HostPlatform): Plan {
	if (platform === 'darwin') {
		return { cmd: 'osascript', args: ['-e', `display notification "${appleScriptString(message)}" with title "${appleScriptString(title)}"`], timeout: 2_000 };
	}
	return { cmd: 'notify-send', args: [title, message], timeout: 2_000 };
}

// --- runners -----------------------------------------------------------------

/** PATH lookup without spawning — cached per process, cleared for tests. */
const _cmdCache = new Map<string, boolean>();
export function hasCommand(cmd: string, env: NodeJS.ProcessEnv = process.env): boolean {
	const cached = _cmdCache.get(cmd);
	if (cached !== undefined) return cached;
	let found: boolean;
	try {
		// `command -v` via sh is the portable probe; execFileSync keeps argv literal.
		execFileSync('sh', ['-c', 'command -v -- "$1"', 'sh', cmd], { env, stdio: ['ignore', 'pipe', 'ignore'], timeout: 2_000 });
		found = true;
	} catch {
		found = false;
	}
	_cmdCache.set(cmd, found);
	return found;
}
export function resetCommandCacheForTests(): void { _cmdCache.clear(); }

export function runPlan(plan: Plan, extraEnv?: NodeJS.ProcessEnv): string {
	return execFileSync(plan.cmd, plan.args, {
		encoding: 'utf-8',
		timeout: plan.timeout ?? 5_000,
		...(plan.input !== undefined ? { input: plan.input } : {}),
		...(extraEnv ? { env: { ...process.env, ...extraEnv } } : {}),
	});
}

/**
 * Open and forget: `xdg-open` may hand off to a browser that inherits our
 * stdio and never exits, so on linux the child is detached. `open` on darwin
 * returns as soon as LaunchServices accepts the request, as before.
 */
export function runOpen(plan: Plan): void {
	if (plan.cmd === 'open') { runPlan(plan); return; }
	const child = spawn(plan.cmd, plan.args, { detached: true, stdio: 'ignore' });
	child.unref();
}

export const currentPlatform = (): HostPlatform => classifyPlatform();
