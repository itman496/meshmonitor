import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type Database from 'better-sqlite3';
import type { BetterSQLite3Database } from 'drizzle-orm/better-sqlite3';
import { MeshCoreRepository, type DbMeshCoreMessage } from './meshcore.js';
import * as schema from '../schema/index.js';
import { createTestDb } from '../../server/test-helpers/testDb.js';

describe('MeshCoreRepository — per-conversation DM history', () => {
  let sqlite: Database.Database;
  let repo: MeshCoreRepository;

  const SELF = 'a'.repeat(64);
  const PEER = 'b'.repeat(64);
  const OTHER = 'c'.repeat(64);

  beforeEach(() => {
    const testDb = createTestDb();
    sqlite = testDb.sqlite;
    repo = new MeshCoreRepository(
      testDb.db as BetterSQLite3Database<typeof schema>,
      'sqlite',
    );
  });

  afterEach(() => {
    sqlite.close();
  });

  async function insert(
    id: string,
    fromPublicKey: string,
    toPublicKey: string | null,
    timestamp: number,
    sourceId: string = 'src-a',
    messageType: string | null = 'text',
  ): Promise<void> {
    const row: DbMeshCoreMessage = {
      id,
      fromPublicKey,
      toPublicKey,
      text: id,
      timestamp,
      messageType,
      createdAt: timestamp,
    };
    await repo.insertMessage(row, sourceId);
  }

  it('matches inbound prefix and outbound full-key DMs without leaking channels, rooms, or other sources', async () => {
    await insert('inbound-prefix', PEER.slice(0, 12), SELF, 100);
    await insert('outbound-full', SELF, PEER, 200);
    await insert('other-peer', SELF, OTHER, 300);
    await insert('legacy-channel', PEER, null, 400);
    await insert('room-post', PEER, 'd'.repeat(64), 500, 'src-a', 'room_post');
    await insert('other-source', SELF, PEER, 600, 'src-b');

    const rows = await repo.getMessagesForConversation(PEER, 20, 'src-a');

    expect(rows.map(row => row.id)).toEqual(['outbound-full', 'inbound-prefix']);
  });

  it('supports offset pagination within one conversation', async () => {
    for (let i = 1; i <= 5; i += 1) {
      await insert(
        `m${i}`,
        i % 2 === 0 ? SELF : PEER.slice(0, 12),
        i % 2 === 0 ? PEER : SELF,
        i,
      );
    }

    const first = await repo.getMessagesForConversation(PEER, 2, 'src-a', 0);
    const second = await repo.getMessagesForConversation(PEER, 2, 'src-a', 2);

    expect(first.map(row => row.id)).toEqual(['m5', 'm4']);
    expect(second.map(row => row.id)).toEqual(['m3', 'm2']);
  });

  it('accepts the 12-character wire prefix and still finds full-key rows', async () => {
    await insert('outbound-full', SELF, PEER, 100);
    await insert('inbound-wire-prefix', PEER.slice(0, 12), SELF, 200);

    const rows = await repo.getMessagesForConversation(
      PEER.slice(0, 12),
      10,
      'src-a',
    );

    expect(rows.map(row => row.id)).toEqual([
      'inbound-wire-prefix',
      'outbound-full',
    ]);
  });
});
