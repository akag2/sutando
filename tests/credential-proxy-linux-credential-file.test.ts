/**
 * Linux credential source for the quota-tracker credential proxy.
 *
 * The proxy read the OAuth credential from the macOS keychain only, so on a
 * Linux host it exited at startup with "No OAuth token found in macOS
 * keychain" even with Claude Code logged in — Claude Code keeps the same
 * `{claudeAiOauth}` document in `<CLAUDE_CONFIG_DIR>/.credentials.json` there.
 * Pins: the candidate order (scoped config dir before the default home), the
 * read shape, and the write-back contract (merge, atomic rename, 0600,
 * read-back guard) that the refresh path relies on to never lose a rotating
 * refresh token.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { credentialFileCandidates, readCredFromFile, writeCredToFile } from '../skills/quota-tracker/scripts/credential-proxy.js';

describe('credential file candidates', () => {
	it('scoped config dir first, default home second, deduplicated', () => {
		assert.deepEqual(credentialFileCandidates('/cfg/x', '/hm'), ['/cfg/x/.credentials.json', '/hm/.claude/.credentials.json']);
		assert.deepEqual(credentialFileCandidates('', '/hm'), ['/hm/.claude/.credentials.json']);
		assert.deepEqual(credentialFileCandidates('/hm/.claude', '/hm'), ['/hm/.claude/.credentials.json']);
	});
});

describe('credential file read/write', () => {
	const dir = mkdtempSync(join(tmpdir(), 'cred-proxy-'));
	const path = join(dir, 'scoped', '.credentials.json');

	it('absent or malformed file reads as null, never throws', () => {
		assert.equal(readCredFromFile(path), null);
		mkdirSync(join(dir, 'scoped'), { recursive: true });
		writeFileSync(path, '{not json');
		assert.equal(readCredFromFile(path), null);
		writeFileSync(path, JSON.stringify({ claudeAiOauth: { refreshToken: 'r' } }));
		assert.equal(readCredFromFile(path), null, 'a document without accessToken is not a credential');
	});

	it('write merges into the existing document, is 0600, and reads back', () => {
		writeFileSync(path, JSON.stringify({ somethingElse: 1, claudeAiOauth: { accessToken: 'old', refreshToken: 'r0' } }));
		const ok = writeCredToFile(path, { accessToken: 'new', refreshToken: 'r1', expiresAt: 123 });
		assert.equal(ok, true);
		const doc = JSON.parse(readFileSync(path, 'utf-8'));
		assert.equal(doc.somethingElse, 1, 'foreign keys survive the write');
		assert.equal(doc.claudeAiOauth.accessToken, 'new');
		assert.equal(doc.claudeAiOauth.refreshToken, 'r1');
		assert.equal(statSync(path).mode & 0o777, 0o600);
		assert.equal(readCredFromFile(path)?.service, path, 'the service identity of a file source is its path');
		assert.equal(readCredFromFile(path)?.oauth.accessToken, 'new');
	});

	it('write creates the parent dir when the scoped config dir is new', () => {
		const fresh = join(dir, 'brand-new', '.credentials.json');
		assert.equal(writeCredToFile(fresh, { accessToken: 'a' }), true);
		assert.equal(readCredFromFile(fresh)?.oauth.accessToken, 'a');
		rmSync(dir, { recursive: true, force: true });
	});
});
