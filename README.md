<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>WikiFusion - Help</title>
  <style>
    body { font-family: sans-serif; line-height: 1.5; }
    kbd { border: 1px solid #999; border-radius: 4px; padding: 0 0.25em; }
    code { background: #f3f3f3; padding: 0 0.2em; border-radius: 3px; }
    .note { background: #f7f7f7; border-left: 4px solid #999; padding: 0.75em 1em; }
    ul { margin-top: 0.25em; }
  </style>
</head>
<body>
  <h1>WikiFusion</h1>

  <p>
    WikiFusion is a fast lookup add-on for NVDA that combines the best parts of <strong>wikiSeek</strong> (Wikipedia)
    and <strong>dictionarySeek</strong> (Wiktionary) into one workflow.
    If you used either of those add-ons before, WikiFusion should feel instantly familiar.
  </p>

  <h2>Quick start</h2>
  <ol>
    <li>Press <kbd>NVDA</kbd>+<kbd>Alt</kbd>+<kbd>I</kbd> to open WikiFusion.</li>
    <li>Type a word or phrase, then press <kbd>Enter</kbd> to search.</li>
    <li>Use the results tree to choose an item, then press <kbd>Enter</kbd> to load it.</li>
  </ol>

  <h2>Smart routing</h2>
  <ul>
    <li><strong>Single word</strong>: Wiktionary first, then Wikipedia if needed.</li>
    <li><strong>Phrase / multiple words</strong>: Wikipedia first, then Wiktionary if needed.</li>
  </ul>

  <h2>The results tree</h2>
  <p>
    Results are shown in a tree with up to two sections:
  </p>
  <ul>
    <li><strong>Wikipedia (N)</strong> - encyclopedia results</li>
    <li><strong>Wiktionary (M)</strong> - dictionary results</li>
  </ul>
  <p>
    Each search automatically expands the sections so you donâ€™t miss results.
    You can collapse a section to reduce scrolling.
  </p>

  <h3>Tree navigation</h3>
  <ul>
    <li><kbd>Up</kbd>/<kbd>Down</kbd>: move through items</li>
    <li><kbd>Right</kbd>: expand a collapsed section</li>
    <li><kbd>Left</kbd>: collapse an expanded section</li>
    <li><kbd>Enter</kbd>:
      <ul>
        <li>On a section header: expand/collapse</li>
        <li>On an article/entry: load it into the article view</li>
      </ul>
    </li>
  </ul>

  <h2>Keyboard shortcuts</h2>
  <ul>
    <li><kbd>NVDA</kbd>+<kbd>Alt</kbd>+<kbd>I</kbd>: open WikiFusion</li>
    <li><kbd>Enter</kbd> (in search box): run search</li>
    <li><kbd>Enter</kbd> (on a result): load selected item</li>
    <li><kbd>Ctrl</kbd>+<kbd>Enter</kbd> (on a result): open the selected item in your browser</li>
    <li><kbd>Ctrl</kbd>+<kbd>C</kbd> (on a result): copy (see below)</li>
  </ul>

  <h2>Copy behaviour (Ctrl+C)</h2>
  <p>
    WikiFusion copies different text depending on the source:
  </p>
  <ul>
    <li><strong>Wikipedia result</strong>: copies the title plus the page URL (useful for sharing or citations).</li>
    <li><strong>Wiktionary result</strong>: copies only the word (useful for spelling and learning).</li>
  </ul>
  <div class="note">
    <p>
      Why the difference? Wiktionary is often used to check spelling and meanings quickly, where pasting the URL is usually noise.
      Wikipedia links, on the other hand, are frequently shared.
    </p>
  </div>

  <h2>Settings</h2>
  <ul>
    <li><strong>Wikipedia language code</strong>: controls which Wikipedia you search (example: <code>en</code>, <code>sv</code>, <code>fr</code>).</li>
    <li><strong>Wiktionary language code</strong>: controls which Wiktionary you search.</li>
    <li><strong>Maximum matches</strong>: default is 50.</li>
    <li><strong>Maximum definitions</strong>: default is 50.</li>
    <li><strong>Sounds</strong>: enable/disable UI sounds.</li>
  </ul>

  <h2>Hotkey note (dictionarySeek)</h2>
  <p>
    WikiFusion uses the same default hotkey as dictionarySeek:
    <kbd>NVDA</kbd>+<kbd>Alt</kbd>+<kbd>I</kbd>.
  </p>
  <p>
    If you still have dictionarySeek installed, the two add-ons will clash. You can either:
  </p>
  <ul>
    <li>Uninstall dictionarySeek, or</li>
    <li>Change the gesture for one of the add-ons in NVDAâ€™s <em>Input Gestures</em> dialog.</li>
  </ul>

  <h3>Why the letter â€œIâ€?</h3>
  <p>
    A bit of trivia: we kept <kbd>I</kbd> because itâ€™s a natural fit for both â€œwikiâ€ and â€œdictionaryâ€ workflows,
    and many users already had the muscle memory from dictionarySeek / wikiSeek.
  </p>

</body>
</html>
