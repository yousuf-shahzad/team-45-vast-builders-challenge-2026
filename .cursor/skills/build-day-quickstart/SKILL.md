---
name: build-day-quickstart
description: >-
  Walk a team through Build Day step by step, once Cursor is connected to the VM over
  SSH. Picks up at Skills, runs the Test Drive loop with them, then hands off to
  Build. Pauses for confirmation after each step instead of dumping the whole guide.
  Use when a team says they're ready to start, asks "what's next", or wants a guided
  run instead of reading `BUILD_DAY.md` themselves.
---

# Build Day Quickstart

This is `BUILD_DAY.md`, run interactively. Kickoff and Access Infra already happened — if the
team isn't connected to the VM from Cursor yet, send them to those two sections first.

## Step 1: Video Search & Summary UI

Point them to the VSS UI from the same lab page — tell them to open it in
their own browser. Have them try a couple
of the suggested search prompts and look at one result: the clip, timestamp, and the
description Cosmos Reason wrote for it. This is the same API the skills call — the point
is to see what's indexed before touching Cursor.

Mention the three tabs (Search, Explore, Dashboard) so they know where to come back and
check things later, then move to Step 2.

## Step 2: Skills

Tell them: the core skills are `ingest/` and `retrieval/`. They don't write REST calls;
they describe what they want and Cursor loads the matching skill. Give one example:
`"find people near the entrance after 6pm"`.

Mention `reingest-videos` / `reingest-chunk` exist, but don't explain re-ingest mechanics
yet — Test Drive covers that by doing it.

Ask: ready to try it for real? Then move to Step 3.

## Step 3: Test Drive

Run this as a live loop with them, one command at a time. Don't batch it.

1. **Look**: have them ask `what's in the index? show me a few examples`.
2. **Search for their idea**: `find the moment where <something they care about>
   happens`. Get ranked segments back; play one to confirm it's right.
3. **Ask instead of search**: `what happens in <one of those videos>?`. Point out the
   difference — moments vs. an answer.
4. **Find a gap**: have them search for something their idea needs that the index
   probably doesn't describe yet (a count, a carried object, a stopped vehicle). Expect
   it to come back empty — that's the point, not a bug.
5. **Re-ingest**: `re-ingest <that video> with a prompt that describes <what they
   need>`. Confirm what Cursor is about to re-run before it runs. This takes a few
   minutes — check the Dashboard tab or ask `is it done yet?` rather than idling.
6. **Search again**: same query as step 4. It should match now.

If any step fails, run the health check (`retrieval/login` + `retrieval/dashboard`). If
that's also unhealthy, hand off to `ask-cosmos` rather than guessing further.

Once the loop works end to end, tell them plainly: the stack is healthy, they're ready
to build.

## Step 4: Build

Hand off, don't drive. Ask: what's the use case, specifically? ("flag someone missing a
hard hat", not "watch for safety issues"). If they're not sure, point them at the video
sources in `ARCHITECTURE_REFERENCE.md#video-corpus-already-indexed`.

Remind them once: web app, CLI, or script only — no native app, the VM can't demo one.

From here they build. Don't keep narrating steps; answer what comes up, and point back
to Test Drive's loop (search → ask → re-ingest → search again) if they get stuck on
whether the data supports their idea.

## Step 5: Demos

Only bring this up if they ask, or late in the day. Submissions start around 4:30pm at
<https://tokensand.com/vastlondon> (`Submit your project`). There's a first round of judging
walking through what they built.

## Agent instructions

1. Confirm Cursor is connected to the VM over SSH before starting. If not, point to
   BUILD_DAY.md sections 1–2 and stop.
2. Go one step at a time. After each step, ask if it worked before moving on — don't
   dump Steps 1–4 at once.
3. Test Drive is a live loop, not a description of one: actually run each of the six
   sub-steps with them in order.
4. If something fails and the health check also fails, hand off to `ask-cosmos` instead
   of troubleshooting further yourself.
5. Once Test Drive passes, stop narrating and let them build. Re-engage only when asked.
