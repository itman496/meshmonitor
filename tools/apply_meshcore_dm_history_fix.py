from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}")
    p.write_text(text.replace(old, new, 1))


def append_text(path: str, text: str) -> None:
    p = Path(path)
    current = p.read_text()
    if text.strip() in current:
        raise RuntimeError(f"{path}: append block already present")
    p.write_text(current.rstrip() + "\n\n" + text.strip() + "\n")


# ---------------------------------------------------------------------------
# Repository: source-scoped, prefix-aware, paginated DM history.
# ---------------------------------------------------------------------------
replace_once(
    "src/db/repositories/meshcore.ts",
    "import { eq, desc, sql, isNull, isNotNull, and, or, lt, gte, inArray, type SQL } from 'drizzle-orm';",
    "import { eq, desc, sql, isNull, isNotNull, and, or, lt, gte, inArray, like, ne, type SQL } from 'drizzle-orm';",
)

replace_once(
    "src/db/repositories/meshcore.ts",
    '''  /**
   * Get messages for a specific conversation (to/from a public key)
   */
  async getMessagesForConversation(publicKey: string, limit: number = 50): Promise<DbMeshCoreMessage[]> {
    const { meshcoreMessages } = this.tables;
    const result = await this.db
      .select()
      .from(meshcoreMessages)
      .where(
        sql`${meshcoreMessages.fromPublicKey} = ${publicKey} OR ${meshcoreMessages.toPublicKey} = ${publicKey}`
      )
      .orderBy(desc(meshcoreMessages.timestamp))
      .limit(limit);
    return this.normalizeBigInts(result) as unknown as DbMeshCoreMessage[];
  }
''',
    '''  /**
   * Get persisted direct messages for one peer, independently of the shared
   * recent-message pool.
   *
   * MeshCore inbound DMs store only the sender's public-key prefix (normally
   * 12 hex characters), while outbound DMs store the peer's full key. Match
   * both forms, scope the read to one source, and exclude broadcast/channel
   * rows plus room posts that happen to name the same author.
   *
   * Results are newest-first. `offset` pages further back into this one
   * conversation for load-older-on-scroll support.
   */
  async getMessagesForConversation(
    publicKey: string,
    limit: number = 100,
    sourceId: string,
    offset: number = 0,
  ): Promise<DbMeshCoreMessage[]> {
    if (!sourceId) {
      throw new Error('MeshCoreRepository.getMessagesForConversation requires a sourceId');
    }
    const { meshcoreMessages } = this.tables;
    const normalizedKey = publicKey.toLowerCase();
    const wirePrefix = normalizedKey.slice(0, 12);
    const peerMatch = or(
      eq(meshcoreMessages.fromPublicKey, normalizedKey),
      eq(meshcoreMessages.fromPublicKey, wirePrefix),
      like(meshcoreMessages.fromPublicKey, `${normalizedKey}%`),
      eq(meshcoreMessages.toPublicKey, normalizedKey),
      eq(meshcoreMessages.toPublicKey, wirePrefix),
      like(meshcoreMessages.toPublicKey, `${normalizedKey}%`),
    );
    const conversationMatch = and(
      isNotNull(meshcoreMessages.toPublicKey),
      peerMatch,
      or(
        isNull(meshcoreMessages.messageType),
        ne(meshcoreMessages.messageType, 'room_post'),
      ),
    );
    const whereClause = and(
      eq(meshcoreMessages.sourceId, sourceId),
      conversationMatch,
    );
    const result = await this.db
      .select()
      .from(meshcoreMessages)
      .where(whereClause)
      .orderBy(desc(meshcoreMessages.timestamp))
      .limit(limit)
      .offset(offset);
    return this.normalizeBigInts(result) as unknown as DbMeshCoreMessage[];
  }
''',
)

# ---------------------------------------------------------------------------
# Manager: map durable DB rows to the same UI message shape as live messages.
# ---------------------------------------------------------------------------
replace_once(
    "src/server/meshcoreManager.ts",
    '''  getRecentMessages(limit: number = 50): MeshCoreMessage[] {
    return this.messages.slice(-limit);
  }

  /**
   * Per-channel message backlog, queried straight from the DB so each channel
''',
    '''  getRecentMessages(limit: number = 50): MeshCoreMessage[] {
    return this.messages.slice(-limit);
  }

  /**
   * Per-conversation DM backlog, queried straight from the DB so a busy public
   * channel cannot evict a quiet conversation from the visible history.
   * Returns oldest-first to match the message stream.
   *
   * `offset` pages further back into this conversation for infinite scroll.
   */
  async getConversationMessages(
    publicKey: string,
    limit: number = 100,
    offset: number = 0,
  ): Promise<MeshCoreMessage[]> {
    const stored = await databaseService.meshcore.getMessagesForConversation(
      publicKey,
      limit,
      this.sourceId,
      offset,
    );
    // DB returns newest-first; reverse to oldest-first for the UI.
    return stored.reverse().map(dbMsg => ({
      id: dbMsg.id,
      fromPublicKey: dbMsg.fromPublicKey,
      fromName: dbMsg.fromName ?? undefined,
      toPublicKey: dbMsg.toPublicKey ?? undefined,
      text: dbMsg.text,
      timestamp: dbMsg.timestamp,
      rssi: dbMsg.rssi ?? undefined,
      snr: dbMsg.snr ?? undefined,
      sourceId: dbMsg.sourceId ?? undefined,
      messageType: dbMsg.messageType ?? undefined,
      // The DB's createdAt is our own observation clock, matching receivedAt.
      receivedAt: dbMsg.createdAt ?? undefined,
      hopCount: dbMsg.hopCount ?? null,
      routePath: dbMsg.routePath ?? null,
      scopeCode: dbMsg.scopeCode ?? null,
      scopeName: dbMsg.scopeName ?? null,
    }));
  }

  /**
   * Per-channel message backlog, queried straight from the DB so each channel
''',
)

# ---------------------------------------------------------------------------
# API: GET one persisted conversation with limit/offset/hasMore.
# ---------------------------------------------------------------------------
replace_once(
    "src/server/routes/meshcoreMessagingRoutes.ts",
    '''/**
 * GET /api/meshcore/messages/channel-counts?channels=0,1,2
''',
    '''/**
 * GET /api/meshcore/messages/conversation/:publicKey
 * Per-conversation DM backlog. Unlike /messages (a global recent tail shared
 * by every channel and DM), this reads the selected peer's durable history.
 *
 * Supports `offset` for infinite-scroll pagination. `publicKey` may be a
 * 12-character wire prefix or a full 64-character key because inbound DMs
 * are stored by prefix.
 */
router.get(
  '/messages/conversation/:publicKey',
  optionalAuth(),
  requirePermission('messages', 'read', { sourceIdFrom: 'params.id' }),
  async (req: Request, res: Response) => {
    try {
      const publicKey = String(req.params.publicKey || '').toLowerCase();
      if (!/^[0-9a-f]{12,64}$/.test(publicKey)) {
        return res.status(400).json({
          success: false,
          error: 'publicKey must be a 12-64 character hex string',
        });
      }
      let limit = parseInt(req.query.limit as string || '100', 10);
      if (isNaN(limit) || limit < 1) {
        limit = 100;
      } else if (limit > VALIDATION.MAX_MESSAGE_LIMIT) {
        limit = VALIDATION.MAX_MESSAGE_LIMIT;
      }
      let offset = parseInt(req.query.offset as string || '0', 10);
      if (isNaN(offset) || offset < 0) {
        offset = 0;
      } else if (offset > VALIDATION.MAX_MESSAGE_OFFSET) {
        offset = VALIDATION.MAX_MESSAGE_OFFSET;
      }

      // The manager returns oldest-first. Fetch one lookahead row; after the
      // reverse, that extra oldest row is index 0 and is removed from this page.
      const page = await managerFor(req, res).getConversationMessages(
        publicKey,
        limit + 1,
        offset,
      );
      const hasMore = page.length > limit;
      const messages = hasMore ? page.slice(1) : page;
      res.json({
        success: true,
        data: messages,
        count: messages.length,
        hasMore,
      });
    } catch (error) {
      logger.error('[API] Error getting MeshCore conversation messages:', error);
      res.status(500).json({ success: false, error: 'Failed to get conversation messages' });
    }
  },
);

/**
 * GET /api/meshcore/messages/channel-counts?channels=0,1,2
''',
)

# ---------------------------------------------------------------------------
# Frontend: fetch selected conversation from DB, merge live messages, paginate.
# ---------------------------------------------------------------------------
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    "import React, { useEffect, useMemo, useState } from 'react';",
    "import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';",
)
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    "import { useSettings } from '../../contexts/SettingsContext';",
    "import { useSettings } from '../../contexts/SettingsContext';\nimport { useCsrfFetch } from '../../hooks/useCsrfFetch';",
)
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    "import { UiIcon } from '../icons';",
    "import { UiIcon } from '../icons';\nimport { compareMeshCoreMessages } from './messageOrder';",
)
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    '''  const { t } = useTranslation();
  const { hasPermission } = useAuth();
''',
    '''  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const csrfFetch = useCsrfFetch();
''',
)
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    '''  const [isCollapsed, setIsCollapsed] = useState<boolean>(false);
  const [mobileShowContent, setMobileShowContent] = useState(false);
''',
    '''  const [isCollapsed, setIsCollapsed] = useState<boolean>(false);
  const [mobileShowContent, setMobileShowContent] = useState(false);
  // Durable backlog for the selected peer, independent of the shared live
  // message pool. The pool remains the source for real-time updates.
  const [history, setHistory] = useState<MeshCoreMessage[]>([]);
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [loadingOlderHistory, setLoadingOlderHistory] = useState(false);
  const historyRef = useRef<MeshCoreMessage[]>([]);
  historyRef.current = history;
  // Reject a late response after the operator switches to another peer.
  const activePeerRef = useRef<string | null>(null);
''',
)
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    '''  // Channel messages carry synthetic `channel-${idx}` keys (see the shared
''',
    '''  // Fetch the selected peer's newest persisted page. This is what keeps old
  // DMs visible after a reload even when public-channel traffic has pushed them
  // out of the global recent-message snapshot.
  useEffect(() => {
    if (!sourceId || !selected) {
      activePeerRef.current = null;
      setHistory([]);
      setHasMoreHistory(false);
      setLoadingOlderHistory(false);
      return;
    }

    let cancelled = false;
    const peer = selected;
    activePeerRef.current = peer;
    setHistory([]);
    setHasMoreHistory(false);
    setLoadingOlderHistory(false);

    void (async () => {
      try {
        const url = `${baseUrl ?? ''}/api/sources/${encodeURIComponent(sourceId)}/meshcore/messages/conversation/${encodeURIComponent(peer)}?limit=200`;
        const response = await csrfFetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (!cancelled && activePeerRef.current === peer) {
          setHistory(data?.success && Array.isArray(data.data)
            ? (data.data as MeshCoreMessage[])
            : []);
          setHasMoreHistory(Boolean(data?.hasMore));
        }
      } catch (err) {
        if (!cancelled && activePeerRef.current === peer) {
          console.error('Failed to fetch MeshCore DM history:', err);
          setHistory([]);
          setHasMoreHistory(false);
        }
      }
    })();

    return () => { cancelled = true; };
  }, [baseUrl, sourceId, selected, csrfFetch, status?.connected]);

  // Load the next older page when MeshCoreMessageStream reports a scroll near
  // the top. Offset is based only on persisted history, not live-only messages.
  const loadOlderHistory = useCallback(() => {
    if (!sourceId || !selected || loadingOlderHistory || !hasMoreHistory) return;
    const peer = selected;
    const offset = historyRef.current.length;
    setLoadingOlderHistory(true);

    void (async () => {
      try {
        const url = `${baseUrl ?? ''}/api/sources/${encodeURIComponent(sourceId)}/meshcore/messages/conversation/${encodeURIComponent(peer)}?limit=100&offset=${offset}`;
        const response = await csrfFetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (activePeerRef.current !== peer) return;
        if (data?.success && Array.isArray(data.data)) {
          const older = data.data as MeshCoreMessage[];
          setHistory(prev => {
            const seen = new Set(prev.map(m => m.id));
            const fresh = older.filter(m => !seen.has(m.id));
            return [...fresh, ...prev];
          });
          setHasMoreHistory(Boolean(data.hasMore));
        } else {
          setHasMoreHistory(false);
        }
      } catch (err) {
        console.error('Failed to load older MeshCore DM history:', err);
        if (activePeerRef.current === peer) setHasMoreHistory(false);
      } finally {
        if (activePeerRef.current === peer) setLoadingOlderHistory(false);
      }
    })();
  }, [
    baseUrl,
    sourceId,
    selected,
    csrfFetch,
    hasMoreHistory,
    loadingOlderHistory,
  ]);

  // Channel messages carry synthetic `channel-${idx}` keys (see the shared
''',
)
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    '''  const filtered = useMemo(() => {
    if (!selected) return [];
    return messages.filter(m => {
      if (!m.toPublicKey) return false;
      if (m.messageType === 'room_post') return false;
      if (isChannelPseudoKey(m.toPublicKey) || isChannelPseudoKey(m.fromPublicKey)) return false;
      if (selfKey && keysMatch(m.fromPublicKey, selfKey) && keysMatch(m.toPublicKey, selected)) return true;
      if (selfKey && keysMatch(m.toPublicKey, selfKey) && keysMatch(m.fromPublicKey, selected)) return true;
      // No selfKey known — fall back to either direction matching the selected peer.
      return keysMatch(m.fromPublicKey, selected) || keysMatch(m.toPublicKey, selected);
    });
  }, [messages, selected, selfKey]);
''',
    '''  // Merge the selected peer's DB backlog with live/socket messages. Dedupe
  // by id and let the live copy win so delivery-status updates stay current.
  const filtered = useMemo(() => {
    if (!selected) return [];
    const matchesSelected = (m: MeshCoreMessage): boolean => {
      if (!m.toPublicKey) return false;
      if (m.messageType === 'room_post') return false;
      if (isChannelPseudoKey(m.toPublicKey) || isChannelPseudoKey(m.fromPublicKey)) return false;
      if (selfKey && keysMatch(m.fromPublicKey, selfKey) && keysMatch(m.toPublicKey, selected)) return true;
      if (selfKey && keysMatch(m.toPublicKey, selfKey) && keysMatch(m.fromPublicKey, selected)) return true;
      // No selfKey known — fall back to either direction matching the selected peer.
      return keysMatch(m.fromPublicKey, selected) || keysMatch(m.toPublicKey, selected);
    };

    const byId = new Map<string, MeshCoreMessage>();
    for (const m of history) {
      if (matchesSelected(m)) byId.set(m.id, m);
    }
    for (const m of messages) {
      if (matchesSelected(m)) byId.set(m.id, m);
    }
    return Array.from(byId.values()).sort(compareMeshCoreMessages);
  }, [history, messages, selected, selfKey]);
''',
)
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    '''  const handleDeleteMessage = async (m: MeshCoreMessage) => {
    if (!window.confirm(t('meshcore.confirm_delete_message', 'Delete this message?'))) return;
    await actions.deleteMessage(m.id);
  };
''',
    '''  const handleDeleteMessage = async (m: MeshCoreMessage) => {
    if (!window.confirm(t('meshcore.confirm_delete_message', 'Delete this message?'))) return;
    const deleted = await actions.deleteMessage(m.id);
    if (deleted) setHistory(prev => prev.filter(row => row.id !== m.id));
  };
''',
)
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    '''    await actions.clearConversation(selected);
  };
''',
    '''    const cleared = await actions.clearConversation(selected);
    if (cleared) {
      setHistory([]);
      setHasMoreHistory(false);
    }
  };
''',
)
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    '''                  conversationKey={`dm-${selected}`}
                  unreadAnchorMs={entryLastRead}
''',
    '''                  conversationKey={`dm-${selected}`}
                  onLoadOlder={loadOlderHistory}
                  hasMoreOlder={hasMoreHistory}
                  loadingOlder={loadingOlderHistory}
                  unreadAnchorMs={entryLastRead}
''',
)

# ---------------------------------------------------------------------------
# Repository regression tests.
# ---------------------------------------------------------------------------
Path("src/db/repositories/meshcore.conversationHistory.test.ts").write_text(r'''import { afterEach, beforeEach, describe, expect, it } from 'vitest';
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
''')

# ---------------------------------------------------------------------------
# Route tests: add method to the shared manager stub, then exercise pagination.
# ---------------------------------------------------------------------------
replace_once(
    "src/server/routes/meshcoreRoutes.test.ts",
    "  getChannelMessages: vi.fn().mockResolvedValue([]),",
    "  getChannelMessages: vi.fn().mockResolvedValue([]),\n  getConversationMessages: vi.fn().mockResolvedValue([]),",
)

route_test_block = r'''
describe('MeshCore conversation history route', () => {
  const PEER = 'a'.repeat(64);

  it('returns a paginated persisted DM page and hasMore', async () => {
    meshcoreManager.getConversationMessages.mockResolvedValueOnce([
      { id: 'lookahead', fromPublicKey: PEER.slice(0, 12), toPublicKey: 'self', text: 'oldest', timestamp: 1 },
      { id: 'visible-older', fromPublicKey: PEER.slice(0, 12), toPublicKey: 'self', text: 'older', timestamp: 2 },
      { id: 'visible-newer', fromPublicKey: 'self', toPublicKey: PEER, text: 'newer', timestamp: 3 },
    ]);

    const response = await authenticatedAgent.get(
      `/api/sources/test-source/meshcore/messages/conversation/${PEER}?limit=2&offset=7`,
    );

    expect(response.status).toBe(200);
    expect(meshcoreManager.getConversationMessages).toHaveBeenCalledWith(
      PEER,
      3,
      7,
    );
    expect(response.body.hasMore).toBe(true);
    expect(response.body.data.map((m: { id: string }) => m.id)).toEqual([
      'visible-older',
      'visible-newer',
    ]);
  });

  it('rejects a non-hex conversation key', async () => {
    const response = await authenticatedAgent.get(
      '/api/sources/test-source/meshcore/messages/conversation/not-a-key',
    );

    expect(response.status).toBe(400);
    expect(meshcoreManager.getConversationMessages).not.toHaveBeenCalled();
  });
});
'''
route_test_path = Path("src/server/routes/meshcoreRoutes.test.ts")
route_test_text = route_test_path.read_text().rstrip()
if not route_test_text.endswith("});"):
    raise RuntimeError("meshcoreRoutes.test.ts: unexpected file ending")
route_test_path.write_text(
    route_test_text[:-3].rstrip()
    + "\n\n  "
    + route_test_block.strip().replace("\n", "\n  ")
    + "\n});\n"
)

replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.test.tsx",
    '''beforeEach(() => {
  csrfFetchMock.mockReset();
  csrfFetchMock.mockResolvedValue(
    new Response(
      JSON.stringify({
        success: true,
        data: { enabled: false, intervalMinutes: 60, lastRequestAt: null },
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ),
  );
});
''',
    '''beforeEach(() => {
  csrfFetchMock.mockReset();
  // Return a fresh Response for each request: selecting a real peer now loads
  // both conversation history and telemetry configuration.
  csrfFetchMock.mockImplementation(() => Promise.resolve(
    new Response(
      JSON.stringify({
        success: true,
        data: { enabled: false, intervalMinutes: 60, lastRequestAt: null },
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ),
  ));
});
''',
)

replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.test.tsx",
    '''    expect(screen.queryByText('Telemetry Retrieval')).toBeNull();
    expect(csrfFetchMock).not.toHaveBeenCalled();
  });

  it('mounts the TelemetryGraphs component with the selected pubkey when sourceId is set', async () => {
''',
    '''    expect(screen.queryByText('Telemetry Retrieval')).toBeNull();
    const urls = csrfFetchMock.mock.calls.map(call => call[0] as string);
    expect(urls.some(url => url.includes('/telemetry-config'))).toBe(false);
  });

  it('mounts the TelemetryGraphs component with the selected pubkey when sourceId is set', async () => {
''',
)

# ---------------------------------------------------------------------------
# Component tests: account for the new conversation fetch and prove persistence
# survives reload plus older-page loading.
# ---------------------------------------------------------------------------
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.test.tsx",
    '''    // Panel made its GET against the per-node telemetry-config endpoint.
    expect(csrfFetchMock).toHaveBeenCalled();
    const calledUrl = csrfFetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain('/api/sources/src-a/meshcore/nodes/');
    expect(calledUrl).toContain(REAL_PK);
    expect(calledUrl).toContain('/telemetry-config');
''',
    '''    // Panel made its GET against the per-node telemetry-config endpoint.
    // The conversation-history request may race it, so find the matching call
    // instead of assuming it is always call zero.
    expect(csrfFetchMock).toHaveBeenCalled();
    const calledUrls = csrfFetchMock.mock.calls.map(call => call[0] as string);
    const telemetryUrl = calledUrls.find(url => url.includes('/telemetry-config'));
    expect(telemetryUrl).toContain('/api/sources/src-a/meshcore/nodes/');
    expect(telemetryUrl).toContain(REAL_PK);
''',
)

component_test_block = r'''
describe('MeshCoreDirectMessagesView — persisted DM history pagination', () => {
  const telemetryResponse = () => new Response(
    JSON.stringify({
      success: true,
      data: { enabled: false, intervalMinutes: 60, lastRequestAt: null },
    }),
    { status: 200, headers: { 'content-type': 'application/json' } },
  );

  it('loads the selected conversation from durable history even when the live pool is empty', async () => {
    const persisted: MeshCoreMessage = {
      id: 'persisted-1',
      fromPublicKey: REAL_PK.slice(0, 12),
      toPublicKey: makeStatus().localNode?.publicKey,
      text: 'persisted hello',
      timestamp: 1000,
      receivedAt: 1001,
    };
    csrfFetchMock.mockImplementation((url: string) => {
      if (url.includes('/messages/conversation/')) {
        return Promise.resolve(new Response(
          JSON.stringify({ success: true, data: [persisted], hasMore: false }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ));
      }
      return Promise.resolve(telemetryResponse());
    });

    render(
      <MeshCoreDirectMessagesView
        messages={[]}
        contacts={[realContact]}
        status={makeStatus()}
        actions={makeActions()}
        baseUrl=""
        sourceId="src-a"
      />,
    );

    fireEvent.click(screen.getByText('Remote Bob'));

    expect(await screen.findByText('persisted hello')).toBeTruthy();
    const urls = csrfFetchMock.mock.calls.map(call => call[0] as string);
    expect(urls.some(url =>
      url.includes(`/meshcore/messages/conversation/${REAL_PK}`)
      && url.includes('limit=200'),
    )).toBe(true);
  });

  it('loads and prepends an older page when the message list is scrolled to the top', async () => {
    const recent: MeshCoreMessage = {
      id: 'recent',
      fromPublicKey: REAL_PK.slice(0, 12),
      toPublicKey: makeStatus().localNode?.publicKey,
      text: 'recent persisted message',
      timestamp: 2000,
      receivedAt: 2001,
    };
    const older: MeshCoreMessage = {
      id: 'older',
      fromPublicKey: makeStatus().localNode?.publicKey ?? '',
      toPublicKey: REAL_PK,
      text: 'older persisted message',
      timestamp: 1000,
      receivedAt: 1001,
    };
    csrfFetchMock.mockImplementation((url: string) => {
      if (url.includes('/messages/conversation/')) {
        const body = url.includes('offset=1')
          ? { success: true, data: [older], hasMore: false }
          : { success: true, data: [recent], hasMore: true };
        return Promise.resolve(new Response(
          JSON.stringify(body),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ));
      }
      return Promise.resolve(telemetryResponse());
    });

    const { container } = render(
      <MeshCoreDirectMessagesView
        messages={[]}
        contacts={[realContact]}
        status={makeStatus()}
        actions={makeActions()}
        baseUrl=""
        sourceId="src-a"
      />,
    );

    fireEvent.click(screen.getByText('Remote Bob'));
    expect(await screen.findByText('recent persisted message')).toBeTruthy();

    const list = container.querySelector('.meshcore-message-list') as HTMLElement;
    fireEvent.scroll(list);

    expect(await screen.findByText('older persisted message')).toBeTruthy();
    expect(screen.getByText('recent persisted message')).toBeTruthy();
    const urls = csrfFetchMock.mock.calls.map(call => call[0] as string);
    expect(urls.some(url =>
      url.includes(`/meshcore/messages/conversation/${REAL_PK}`)
      && url.includes('offset=1'),
    )).toBe(true);
  });
});
'''
append_text(
    "src/components/MeshCore/MeshCoreDirectMessagesView.test.tsx",
    component_test_block,
)

print("Applied MeshCore DM history pagination source and test changes.")
