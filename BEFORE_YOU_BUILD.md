# Before you build

## Do these before you arrive

**1. [Join the Cosmos Community](https://community.vastdata.com?utm_campaign=event26-builders-challenge).** Sign up to access the Builders Challenge infrastructure. You'll connect to the VAST environment (no local setup required).

**2. [Sign up for Cursor](https://cursor.com/), using the personal email you applied with.** We add your credits to the personal email you sign up with before the day. No sign up means no credits. **A personal email is preferred to avoid IT issues.**

**3. Create a [wandb account](https://wandb.ai/).** We will provide access to serverless inference at the start of the event to get you started. If you need more credits during the event, the Weights & Biases by CoreWeave team will use your account email to update your subscription as needed.

**4. Form a team.** Up to four people. Come on your own and we'll help you find one.

**5. Bring your ideas.** It's the most useful thing you can prepare. See more below.

For the build day, you need to bring your laptop and charger. A personal laptop is preferred over a work laptop, since a work laptop's VPN or IT restrictions may get in the way.

## What you're building

**A video agent**: an app that understands video and does something useful with it. Search hours of footage in plain words, ask what happened, spot events, or trigger an action when something matters.

**One idea, working, shipped by the end of the day.**

[Video Search and Summary UI](https://www.vastdata.com/blog/unlock-video-intelligence-build-real-time-video-search-and-ai-summary) is a comprehensive example of what you can build on this stack, and a quick way to see what's indexed. [Check out a video search and summary overview video](https://drive.google.com/file/d/1kMiMJCX-4YDmtw8RuRYKg8Pv76dKcC4C/view?usp=sharing) (videos blurred for public consumption).

## The Builders Stack

- **[VAST AI OS](https://www.vastdata.com/platform/ai-os)** implements the video ingestion pipeline at scale. Vectors, metadata, and video data are in one unified platform.
- **[NVIDIA Cosmos](https://www.nvidia.com/en-us/ai/cosmos/)** describes and generates vector embeddings for each video segment, and **[YOLO](https://docs.ultralytics.com/models/yolo11#overview)** finds objects.
- **[CoreWeave](https://coreweave.com/)** provides the GPUs the models run on. [Weights & Biases](https://wandb.ai/) by CoreWeave provides [serverless LLM inference](https://docs.wandb.ai/inference) for your own app's reasoning and experiment tracking, observability, and auto research through ARIA and Weave.
- **[Cursor](https://cursor.com/)** is how you build, and where you'll spend the day. We'll share skills that drive the whole stack in plain language, so you describe what you want instead of wrangling endpoints.

## Think about your use case

Pick a problem worth solving with video, and be specific. For a construction site agent, "flag someone missing a hard hat" is something you can build. "Watch for safety issues" isn't.

**Hold the use case loosely.** You'll see what footage is provided and indexed on build day, and your idea may need to adjust to fit it.

> 💡 Every video segment gets a description written by a model following an initial prompt, and anything that prompt didn't ask about isn't in there, so you can't search for it later. During build day you'll be able to ingest with a different prompt if you need to.

Once you're ready, here is the [build day guide](BUILD_DAY.md).
