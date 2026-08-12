from pathlib import Path

path = Path('src/components/MeshCore/MeshCoreDirectMessagesView.tsx')
text = path.read_text()

replacements = [
    (
        "  }, [baseUrl, sourceId, selected, csrfFetch, status?.connected]);",
        "  }, [baseUrl, normalizedSourceId, selected, csrfFetch, status?.connected]);",
    ),
    (
        "    baseUrl,\n    sourceId,\n    selected,\n    csrfFetch,",
        "    baseUrl,\n    normalizedSourceId,\n    selected,\n    csrfFetch,",
    ),
]

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'expected exactly one dependency-array match, found {count}: {old!r}')
    text = text.replace(old, new, 1)

path.write_text(text)
print('Corrected MeshCore DM history hook dependencies.')
