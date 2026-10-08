id: alice
keys: alice, alice core, alice_core, cognitive engine
---

location : snowgate-alice/alice_core.js invoked through node; sentence retrieval in hydra_cli/alice_retrieve.py.

order : typo table, pending clarification, vagueness rule, skill index, alice_core strict routes, sentence retrieval, remaining alice_core routes, then a hydra summon.

local routes : arithmetic, logic, canon, system commands, and exact recall return without a provider call.

retrieval index : sqlite at ~/.hydra/alice_index.db; sentences and paragraphs from the easylm stacks, THINKERS.md, and every whitelisted page fetched so far.

relation weights : rare term, phrase, entity, alias, derived form, related term, heading, paragraph context, pronoun coreference, lifespan, definition, answer slot, answer-type cue, lead paragraph, source tier; weights add.

threshold : a sentence answers when its weight sum reaches 0.6 of the query mass, the anchor from the question's own relations reaches half the threshold, and covered question-term weight reaches 0.7.

whitelist harvest : wikidata names people and things; wikipedia, plato.stanford.edu, and developer.mozilla.org pages enter the index; deny list overrides.

gap : below threshold after harvest, alice asks one clarifying question and adds the reply's terms to the same sum; two rounds, then DONT_KNOW or a summon.

cli : python -m hydra_cli.alice_retrieve build | stats | ask QUESTION | chat.

tools on summon : read_file, write_file, edit_file, list_dir, grep_search, find_files, run_command.
