import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// Security regression guard for `pressKeyTool` / `switchAppTool` in
// src/inline-tools.ts and the darwin plans in src/platform-desktop.ts.
//
// Pre-fix: pressKeyTool accepted an optional `app` parameter and embedded
// it verbatim in `osascript -e 'tell application "${app}" to
// activate'`. A value containing `"` could break out of the AppleScript
// string literal and inject arbitrary AppleScript, which can shell out
// via `do shell script "..."` — arbitrary code execution from a tool-
// call argument.
//
// The escape now lives in ONE place — `appleScriptString()` in
// platform-desktop.ts — and every darwin plan that embeds a caller value
// in a `tell application "..."` literal must go through it. The tools
// themselves must never build the AppleScript inline again. Pin both
// halves so a future refactor that re-introduces raw interpolation on
// either side fails here.

const ROOT = join(import.meta.dirname ?? '.', '..');
const SRC = readFileSync(join(ROOT, 'src/inline-tools.ts'), 'utf-8');
const DESKTOP = readFileSync(join(ROOT, 'src/platform-desktop.ts'), 'utf-8');

describe('inline-tools pressKey app activation — AppleScript injection guard', () => {
	it('does not interpolate raw `app` into an osascript tell-application command anywhere', () => {
		for (const [name, text] of [['inline-tools.ts', SRC], ['platform-desktop.ts', DESKTOP]] as const) {
			assert.doesNotMatch(
				text,
				/tell application "\$\{(app|processName)\}" to activate/,
				`src/${name} contains the raw \`tell application "\${app}"\` pattern again — ` +
					'a tool-call argument containing `"` would inject AppleScript and (via ' +
					'`do shell script`) execute arbitrary commands. Route through appleScriptString().',
			);
		}
	});

	it('pressKey app-activation branch delegates to activateAppPlan (no inline AppleScript)', () => {
		const pressKeyBranch = SRC.match(/pressKeyTool[\s\S]*?Activate target app[\s\S]{0,900}/);
		assert(pressKeyBranch, 'could not locate pressKeyTool app-activation branch');
		assert.match(pressKeyBranch[0], /activateAppPlan\(app/);
		assert.doesNotMatch(pressKeyBranch[0], /osascript/);
	});

	it('switchAppTool delegates to activateAppPlan (no inline AppleScript)', () => {
		const branch = SRC.match(/switchAppTool[\s\S]{0,1500}/);
		assert(branch);
		assert.match(branch[0], /activateAppPlan\(app/);
		assert.doesNotMatch(branch[0], /tell application/);
	});

	it('the darwin activate plan escapes app AND process name via appleScriptString', () => {
		const plan = DESKTOP.match(/export function activateAppPlan[\s\S]*?\n}\n/);
		assert(plan, 'could not locate activateAppPlan');
		assert.match(plan[0], /tell application "\$\{appleScriptString\(app\)\}" to activate/);
		assert.match(plan[0], /process "\$\{appleScriptString\(processName\)\}"/);
	});

	it('appleScriptString strips the AppleScript literal delimiters (backslash, then double-quote)', () => {
		// execFileSync bypasses the shell — only AppleScript string literal escaping needed:
		// backslash and double-quote. Single-quote shell escaping is explicitly NOT required.
		assert.match(
			DESKTOP,
			/export function appleScriptString[\s\S]+?\.replace\(\/\\\\\/g,\s*'\\\\\\\\'\)\.replace\(\/"\/g,\s*'\\\\"'\)/,
			'appleScriptString must chain `.replace(/\\\\/g, \'\\\\\\\\\')` then `.replace(/"/g, \'\\\\"\')`',
		);
	});

	it('uses execFileSync (not execSync) for osascript calls — no shell layer', () => {
		// execFileSync passes the AppleScript string as a direct argument to the osascript
		// binary, bypassing the shell entirely. This is safer than execSync which spawns a
		// shell and requires shell-level string escaping on top of AppleScript escaping.
		for (const text of [SRC, DESKTOP]) {
			assert.ok(!text.includes('execSync(`osascript') && !text.includes("execSync('osascript"));
		}
		assert.match(DESKTOP, /execFileSync\(plan\.cmd, plan\.args/);
		assert.match(SRC, /execFileSync\('osascript'/, 'the remaining darwin-only tools still use execFileSync');
	});
});
