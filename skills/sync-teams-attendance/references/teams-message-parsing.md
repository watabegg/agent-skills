# Teams punch message parsing

## Effective content

Use the message's current effective content and its original `<time datetime>` value. An edit does not create a second punch.

Teams may expose an edited message in any of these equivalent forms:

- a DOM subtree where the superseded text has `text-decoration-line: line-through`;
- `<s>`, `<strike>`, or `<del>` around the superseded text;
- text snapshots such as `~中断します~終了します` or `~~中断します~~終了します`;
- a style-free fallback such as `中断します終了します`.

Remove struck-through DOM nodes before extracting text. Classification then follows these rules in order:

1. Match the complete normalized text to one configured phrase.
2. Remove tilde-delimited superseded text and match the remainder.
3. If styling and delimiters were lost, segment the entire normalized text into configured phrases and use the last phrase only when at least two phrases consume the whole text.
4. Otherwise classify the message as unrelated.

The full-consumption rule prevents ordinary prose containing a punch phrase from becoming attendance data. Do not use substring matching.

## Collection identity

Prefer Teams' `data-message-id` as the record key. When it is unavailable, use the timestamp and author so a re-rendered edit replaces the earlier snapshot instead of becoming a second event.

## Required fixtures

Keep self-test coverage for:

- an ordinary punch phrase;
- single-tilde and double-tilde edit snapshots;
- a concatenated old/new phrase fallback;
- unrelated text containing punctuation between punch phrases;
- a complete start-to-edited-end interval.
