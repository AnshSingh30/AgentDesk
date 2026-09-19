# Annotation replies

Annotations are curated question → answer pairs attached to an agent. When an incoming question is
similar enough to an annotated one, the agent replies with your answer **verbatim**: no retrieval, no SQL
and no model call. Use them for answers that must be exact (a refund policy, an emergency number, a
legally reviewed statement), for frequent questions you want answered for free and instantly, and to fix
a bad answer straight away while you improve the documents behind it.

## Managing annotations

- **Annotations page**: open an agent → *Annotations* (Editor role to add, edit or delete; anyone can
  view). Each annotation shows how often it has been used.
- **From the Playground**: click the bookmark icon under any reply. The question and the reply are
  prefilled, so you can edit the answer and save it.
- **Settings**: builder → *Guardrails & Memory* → *Annotation replies* has the on/off switch and the
  similarity threshold (default **0.90**, range 0.50–1.00). Changes take effect when you publish.

## How matching works

After the input guardrails pass, the `annotation_reply` step embeds the user's message and compares it
(cosine similarity) with every annotated question of the agent, up to 500 per agent. Question embeddings
are cached, so they are computed once. If the best score reaches the threshold, the stored answer is sent
and the route is recorded as `annotation reply (similarity 0.97)` in the debug panel and conversation
log. Otherwise the run continues normally.

Blocked messages never reach annotations, so guardrails always run first. Annotations are read live
rather than versioned: adding one affects the published agent immediately, just like editing a
knowledge base document.

Too many false matches means the threshold is too low; raise it towards 0.95. Paraphrases that don't
match mean it's too high, so lower it, or add the paraphrase as another annotation.
