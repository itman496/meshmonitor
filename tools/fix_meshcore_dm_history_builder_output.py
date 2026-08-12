from __future__ import annotations

import subprocess
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}")
    p.write_text(text.replace(old, new, 1))


# The first builder version appended the route tests by reopening the file's
# final brace. Restore that test file and insert the focused describe block at a
# stable marker inside the existing top-level `describe('MeshCore Routes')`.
subprocess.run(
    ["git", "restore", "src/server/routes/meshcoreRoutes.test.ts"],
    check=True,
)

replace_once(
    "src/server/routes/meshcoreRoutes.test.ts",
    "  getChannelMessages: vi.fn().mockResolvedValue([]),",
    "  getChannelMessages: vi.fn().mockResolvedValue([]),\n  getConversationMessages: vi.fn().mockResolvedValue([]),",
)

route_tests = r'''  describe('GET /api/sources/test-source/meshcore/messages/conversation/:publicKey', () => {
    const PEER = 'a'.repeat(64);

    beforeEach(() => {
      meshcoreManager.getConversationMessages.mockReset();
      meshcoreManager.getConversationMessages.mockResolvedValue([]);
    });

    it('returns a paginated persisted DM page and hasMore', async () => {
      meshcoreManager.getConversationMessages.mockResolvedValueOnce([
        {
          id: 'lookahead',
          fromPublicKey: PEER.slice(0, 12),
          toPublicKey: 'self',
          text: 'oldest',
          timestamp: 1,
        },
        {
          id: 'visible-older',
          fromPublicKey: PEER.slice(0, 12),
          toPublicKey: 'self',
          text: 'older',
          timestamp: 2,
        },
        {
          id: 'visible-newer',
          fromPublicKey: 'self',
          toPublicKey: PEER,
          text: 'newer',
          timestamp: 3,
        },
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

replace_once(
    "src/server/routes/meshcoreRoutes.test.ts",
    "  describe('GET /api/sources/test-source/meshcore/status', () => {",
    route_tests + "  describe('GET /api/sources/test-source/meshcore/status', () => {",
)

# Be defensive about optional props. A few test/render paths can surface the
# string "undefined" while no source is selected; never construct a history URL
# unless a real source id exists.
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    "  const activePeerRef = useRef<string | null>(null);",
    '''  const activePeerRef = useRef<string | null>(null);
  const normalizedSourceId =
    typeof sourceId === 'string' && sourceId.length > 0 && sourceId !== 'undefined'
      ? sourceId
      : '';''',
)
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    "    if (!sourceId || !selected) {",
    "    if (!normalizedSourceId || !selected) {",
)
replace_once(
    "src/components/MeshCore/MeshCoreDirectMessagesView.tsx",
    "    if (!sourceId || !selected || loadingOlderHistory || !hasMoreHistory) return;",
    "    if (!normalizedSourceId || !selected || loadingOlderHistory || !hasMoreHistory) return;",
)

component_path = Path("src/components/MeshCore/MeshCoreDirectMessagesView.tsx")
component_text = component_path.read_text()
source_call_count = component_text.count("encodeURIComponent(sourceId)")
if source_call_count != 2:
    raise RuntimeError(
        "MeshCoreDirectMessagesView.tsx: expected two generated source-id URL uses, "
        f"found {source_call_count}"
    )
component_path.write_text(
    component_text.replace(
        "encodeURIComponent(sourceId)",
        "encodeURIComponent(normalizedSourceId)",
    )
)

print("Corrected route-test insertion and optional-source guard.")
