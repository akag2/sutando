/**
 * Host-platform identity for the TypeScript services — the one place that
 * answers "is this a platform Sutando can run on" and "which desktop backend
 * do the inline tools use here".
 *
 * Sutando was macOS-only: voice-agent.ts exited on any non-darwin platform,
 * and every inline tool shelled out to osascript / pbcopy / open. Linux is now
 * a supported host (Debian/Ubuntu with an X11 desktop — a real one or an Xvfb
 * virtual one). The gate below admits darwin and linux; anything else still
 * refuses, because nothing has been verified there.
 *
 * Pure functions over an injectable platform string so the decision is
 * testable without spawning a process on the "wrong" OS.
 */

export type HostPlatform = 'darwin' | 'linux' | 'unsupported';

/** Classify a Node `process.platform` value. */
export function classifyPlatform(platform: string = process.platform): HostPlatform {
	if (platform === 'darwin') return 'darwin';
	if (platform === 'linux') return 'linux';
	return 'unsupported';
}

export function isDarwin(platform: string = process.platform): boolean {
	return classifyPlatform(platform) === 'darwin';
}

export function isLinux(platform: string = process.platform): boolean {
	return classifyPlatform(platform) === 'linux';
}

/**
 * The refusal message for an unsupported host, or null when the platform is
 * one Sutando runs on. SUTANDO_TEST_MODE=1 keeps the old escape hatch so the
 * test suite can boot the agent anywhere.
 */
export function unsupportedPlatformMessage(
	platform: string = process.platform,
	env: NodeJS.ProcessEnv = process.env,
): string | null {
	if (env.SUTANDO_TEST_MODE === '1') return null;
	if (classifyPlatform(platform) !== 'unsupported') return null;
	return `Sutando requires macOS or Linux (got ${platform})`;
}

/**
 * True when a graphical session is reachable: always on darwin; on linux only
 * with an X11 or Wayland display in the environment. Tools that drive the
 * desktop (screenshots, keystrokes, clipboard) check this before spawning.
 */
export function hasDisplay(
	platform: string = process.platform,
	env: NodeJS.ProcessEnv = process.env,
): boolean {
	if (classifyPlatform(platform) === 'darwin') return true;
	if (classifyPlatform(platform) === 'linux') return Boolean(env.DISPLAY || env.WAYLAND_DISPLAY);
	return false;
}
