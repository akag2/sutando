/**
 * A denied keystroke names the setup step, the way a denied scroll already does.
 *
 * type_text and press_key returned the raw osascript error, so the model told
 * the user "I don't have permission" with nothing to act on — while `scroll`,
 * two files away, had been turning the same denials into spoken steps.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { keystrokeOutcome } from '../src/osascript-setup-hint.js';

const SRC = readFileSync(
	join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'inline-tools.ts'),
	'utf8',
);

const ACCESSIBILITY =
	'36:48: execution error: System Events got an error: osascript is not allowed to send keystrokes. (1002)';
const AUTOMATION =
	'0:0: execution error: Not authorized to send Apple events to Google Chrome. (-1743)';

describe('keystrokeOutcome', () => {
	it('an Accessibility denial becomes steps the model is told to read aloud', () => {
		const r = keystrokeOutcome('Type', ACCESSIBILITY);
		assert.equal((r as { status: string }).status, 'setup_required');
		assert.match((r as { steps: string[] }).steps[0], /Privacy & Security > Accessibility/);
		assert.match((r as { message: string }).message, /verbatim/);
	});

	it('an Automation denial does too, and names that pane instead', () => {
		const r = keystrokeOutcome('press_key', AUTOMATION);
		assert.equal((r as { status: string }).status, 'setup_required');
		assert.match((r as { steps: string[] }).steps[0], /Privacy & Security > Automation/);
	});

	it('an ORDINARY failure stays a raw error — a real bug must not read as setup', () => {
		const r = keystrokeOutcome('Paste', 'Command failed: pbcopy\nbroken pipe');
		assert.equal((r as { status?: string }).status, undefined);
		assert.match((r as { error: string }).error, /^Paste: Command failed: pbcopy/);
	});

	it('the prefix identifies which tool failed, so the model does not guess', () => {
		assert.match((keystrokeOutcome('Type', ACCESSIBILITY) as { message: string }).message, /^Type /);
		assert.match((keystrokeOutcome('press_key', ACCESSIBILITY) as { message: string }).message, /^press_key /);
	});
});

describe('the keystroke tools route their failures through it', () => {
	it('no catch in inline-tools.ts returns a bare `<tool> failed:` string', () => {
		// The regression this pins: each site formatted its own error and the
		// hint was never consulted.
		assert.doesNotMatch(SRC, /error: `press_key failed:/);
		assert.doesNotMatch(SRC, /error: `Paste failed:/);
		assert.doesNotMatch(SRC, /error: `Type failed:/);
	});

	it('every keystroke failure site routes through keystrokeOutcome with its tool prefix', () => {
		// One press_key site (the key-code/keystroke split now lives in the
		// platform backend, so there is a single send) and both type_text
		// branches (paste, type). Pinned by prefix so a site cannot silently
		// start returning a bare string — counting branches pinned the
		// implementation shape, not the behaviour.
		const pressKey = SRC.match(/export const pressKeyTool[\s\S]*?\n};\n/)?.[0] ?? '';
		const typeText = SRC.match(/export const typeTextTool[\s\S]*?\n};\n/)?.[0] ?? '';
		assert.match(pressKey, /return keystrokeOutcome\('press_key'/);
		assert.match(typeText, /return keystrokeOutcome\('Paste'/);
		assert.match(typeText, /return keystrokeOutcome\('Type'/);
		for (const [name, body] of [['pressKeyTool', pressKey], ['typeTextTool', typeText]] as const) {
			assert.ok(body.length > 0, `could not locate ${name}`);
			assert.doesNotMatch(body, /return \{ error: `(press_key|Paste|Type)/, `${name} formats its own keystroke error`);
		}
	});
});
