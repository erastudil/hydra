# Getting started with Hydra

This guide is for you if you have used ChatGPT, Claude, or Gemini in a web browser, and you want that kind of conversation on your own computer. You need to know how to type, copy, paste, and use a browser. That is the whole list.

Hydra is a program you talk to in a text window called the terminal. It sends your question to an AI company you choose, then prints the answer. One Hydra can reach free accounts and paid ones. You can stop after the free account. The paid parts are here for the day you want them.

Prices and button names below were read from each company's own pages on October 15, 2026. If a page has moved, use the site's search box. The price on the checkout page is the one you will pay.

## The short path

Do these, and you can chat today. The later sections add money, more companies, and a model that runs on your computer.

1. Open a terminal. The steps are in the next section.
2. Install Python, then Hydra.
3. Create a free Cloudflare account and save the two codes with `hydra auth cloudflare`.
4. Ask one question: `hydra free "Say hello in one sentence."`
5. Open a conversation that remembers what you just said: `hydra agent --free`

When that answer appears, you are set up. Come back for credits and other accounts when you want them.

## Words you will see

**Terminal.** A window that waits for you to type a line and press Enter. Windows calls it PowerShell or Terminal. Mac and Linux call it Terminal.

**Command.** One line you type. Press Enter to run it. The computer answers underneath, then waits again.

**Paste.** Windows and Linux: Ctrl+V. Mac: Command+V. If nothing appears, try right-click, or Shift+Ctrl+V.

**Account.** The email login on a website such as Cloudflare or OpenRouter.

**API key.** A long password the website shows you so Hydra can use your account. Treat it like the password for your bank. Anyone who has the key can spend your free allowance or your credits.

**Credits.** Prepaid money that sits at that website. Each question spends a little. Hydra does not have its own wallet, and it never sees your card.

**Model.** The particular AI that answers. Hydra gives the famous ones short nicknames, such as `sonnet 5.5`.

**Provider.** The company that runs the model. Cloudflare, OpenRouter, Vercel, Cheaper Inference, and Hugging Face are providers.

**Endpoint.** An address for a model you rent, usually on RunPod or Modal. You can ignore this word until the last paid section.

## Open a terminal

### Windows

1. Click the Start button.
2. Type `PowerShell`.
3. Open **Windows PowerShell**. Windows Terminal is the same kind of window, and it is fine too.
4. You should see a line that ends with `>`.

### Mac

1. Press Command and the space bar together. That opens Spotlight.
2. Type `Terminal` and press Return.
3. You should see a line that ends with `%` or `$`.

### Linux

1. Open the app menu.
2. Search for Terminal.
3. Open it. You should see a line that ends with `$` or `%`.

Leave this window open. The rest of the guide is lines you paste into it.

If a later command says `not recognized` or `command not found`, the program is missing, or this window was opened before the install finished. Close the window, open a new one, and try the command again.

## Install Python

Hydra needs a free program called Python. You install it once.

### Windows

1. Open https://www.python.org/downloads/
2. Choose the download button for the latest Python 3.
3. Run the installer.
4. On the first screen, turn on the box that says **Add python.exe to PATH**. That box is easy to miss. With the box on, the terminal can find Python.
5. Choose **Install Now**.
6. Close PowerShell and open it again.
7. Type the next line and press Enter.

```text
python --version
```

You should see `Python 3` and a number. Python 3.8 or newer is enough.

### Mac

1. Open Terminal.
2. Type `python3 --version` and press Return.

If you see Python 3.8 or newer, Python is already there. Skip the download.

If the command is missing, open https://www.python.org/downloads/ , download the macOS installer, run it, then close Terminal and open it again. Check with `python3 --version`.

### Linux

Type:

```text
python3 --version
```

If you see Python 3.8 or newer, you are done with this step.

On Ubuntu and similar systems, this installs it when the command is missing. The password you type will not appear. That is normal. Press Enter after the password.

```text
sudo apt install python3 python3-pip
```

## Install Hydra

This downloads Hydra's installer from the project's page on GitHub and runs it. If you want to read the installer first, open the link in your browser, then come back and paste the command.

Mac and Linux. Paste this and press Enter:

```text
curl -fsSL https://raw.githubusercontent.com/erastudil/hydra/main/install.sh | bash
```

The installer file, if you want to look at it: https://raw.githubusercontent.com/erastudil/hydra/main/install.sh

Windows PowerShell. Paste this and press Enter:

```text
irm https://raw.githubusercontent.com/erastudil/hydra/main/install.ps1 | iex
```

The installer file: https://raw.githubusercontent.com/erastudil/hydra/main/install.ps1

If Windows says running scripts is disabled, paste this, press Enter, and then paste the install line again. It applies only to this window.

```text
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

When the installer finishes, close the terminal and open a new one. Check the install:

```text
hydra --version
```

You should see `hydra` and a version number.

On Mac or Linux, a missing `hydra` command means the terminal cannot see the new program yet. Paste this, press Enter, and try `hydra --version` again:

```text
export PATH="$HOME/.local/bin:$PATH"
```

That line lasts until you close the window. To keep it, add the same line to the file `.zshrc` in your home folder on a Mac, or `.bashrc` on many Linux systems. You can do that later. Today's window only needs the one line.

Windows adds Hydra for you. A new PowerShell window is what picks it up.

Print this guide again any time:

```text
hydra setup
```

## Save a key without showing it

An API key is a password made of a long random string. Hydra stores yours in one file that belongs to your user account:

- Windows: `C:\Users\YourName\.hydra\.env`
- Mac: `/Users/YourName/.hydra/.env`
- Linux: `/home/YourName/.hydra/.env`

The folder name starts with a dot, so file browsers hide it. The file is still there.

The safe way to fill it in is the wizard. Type:

```text
hydra auth
```

It lists the companies. Pick a number. When it asks for the key, paste, then press Enter. The letters stay hidden. That is what you want. A screenshot of that moment is safe, because the key is not on the screen. A screenshot of the website page that shows the key is a picture of your password.

You can also go straight to one company:

```text
hydra auth cloudflare
hydra auth openrouter
```

Press Ctrl+C if you want to cancel.

See what is saved. This shows the first and last few characters, and hides the middle:

```text
hydra auth status
```

Check that a company accepts the key:

```text
hydra auth test cloudflare
```

The same test works with `openrouter`, `huggingface`, `vercel`, `cheaperinference`, and `runpod`.

### What an environment variable is

A tutorial on the web may say "set the environment variable `OPENROUTER_API_KEY`". That means "give this name a secret value." The wizard does it for you. The file ends up with one fact per line:

```text
CLOUDFLARE_API_TOKEN=paste-the-token-here
CLOUDFLARE_ACCOUNT_ID=paste-the-account-id-here
OPENROUTER_API_KEY=sk-or-paste-the-key-here
```

Those three values are examples. A real key is longer, and you get it from the website. Leave no spaces around the `=` sign. Leave the quotes off. One key per line.

You can edit that file yourself if the wizard feels worse than Notepad.

1. Open the hidden folder.
   - Windows: open File Explorer, click the address bar, paste `%USERPROFILE%\.hydra`, and press Enter.
   - Mac: in Finder, choose Go, then Go to Folder, and paste `~/.hydra`.
   - Linux: in the file manager, press Ctrl+H to show hidden folders, then open `.hydra` in your home folder.
2. Open the file named `.env`. If it does not exist yet, create it with Notepad, TextEdit, or the text editor you already have. TextEdit on a Mac should be in plain-text mode.
3. Add the line, save, and close the editor.

On Mac and Linux, the wizard also marks the file so other login accounts on the computer cannot read it. On Windows, the file sits in your personal user folder. Anyone who can sign in as you can read it. A shared Windows login is a shared key.

### Habits that keep the key safe

- Paste the key into `hydra auth`, or into the `.env` file. Leave it out of the command itself. A line such as `hydra auth set OPENROUTER_API_KEY sk-or-...` can be saved in the terminal's history, where someone can scroll back and read it.
- Leave the key out of email, chat, screenshots, and any file you might upload or zip up for someone.
- The website often shows a new key once. Paste it into Hydra before you close the tab. If you lose it, create a new key on the website and delete the old one.
- If a key appears somewhere public, delete that key on the website, create a new one, and run `hydra auth` again. The new line replaces the old one.
- A `.env` file inside a project folder is the wrong place for these keys. Hydra skips secrets it finds there. Your real keys belong in the home file above. Leave that safety on. There is a switch called `HYDRA_TRUST_CWD_ENV` that turns the safety off. You can leave the switch alone.
- A tutorial may open Windows Settings and a box called Environment Variables. You can close that box. A key typed there is offered to every program you run, and the boxes are easy to mix up. Hydra's file is the smaller place.

A key typed with `export` on Mac or Linux, or `$env:...` in PowerShell, lasts until you close that window, and it beats the file while the window is open. Close the window when you are finished, and the file is what Hydra uses next time.

## Free account: Cloudflare

Cloudflare Workers AI is the closest thing to a free chat account. Every account gets 10,000 Neurons a day at no charge. A Neuron is their unit of AI work. A short conversation uses a small part of that, and the allowance resets every day.

The free account is enough for the daily allowance. The paid Workers plan is optional. On that plan, use past the daily 10,000 Neurons costs $0.011 per 1,000 Neurons. Hydra does not bill you. Cloudflare does, and only if you upgrade.

Their steps, with pictures: https://developers.cloudflare.com/workers-ai/get-started/rest-api/

1. Create a free account at https://dash.cloudflare.com/sign-up
2. Open Workers AI. The dashboard search box will find it if the menus have moved. A direct hop is https://dash.cloudflare.com/?to=/:account/ai/workers-ai
3. Choose **Use REST API**.
4. Choose **Create a Workers AI API Token**. Look at the permissions it already filled in. Choose **Create API Token**.
5. Choose **Copy API Token**. Leave the tab open.
6. On the same page, copy the **Account ID**. It is a long mix of letters and numbers.
7. In the terminal:

```text
hydra auth cloudflare
```

8. It offers to open the website. Type `n` and press Enter if you already have the tab.
9. Paste the token when it asks for `CLOUDFLARE_API_TOKEN`. Paste the account id when it asks for `CLOUDFLARE_ACCOUNT_ID`. Both pastes stay hidden. Press Enter after each one.
10. If it says the key was rejected, type `n` so it does not save the bad copy. Go back to the website, copy again, and rerun the command.

Ask a question:

```text
hydra free "In one sentence, what is a terminal window?"
```

Words should appear. That is the answer. If Hydra says a credential is missing, run `hydra auth status` and look for both Cloudflare lines. Each saved line shows a short mask such as `abcd...wxyz`. `[NOT SET]` means that line still needs a value.

## Free account: OpenRouter

OpenRouter is a switchboard. One login reaches many models. Models whose names end in `:free` cost nothing in tokens. They have a cap:

- 20 requests a minute
- 50 requests a day, until the account has purchased at least $10 of credits over its whole life
- 1,000 requests a day after that $10 purchase, still 20 per minute

A balance below zero pauses the free models until you add credits. The free models are for learning and for ordinary questions. A job that must run all day wants a paid balance.

New accounts also get a small allowance to try the paid models. Read the number on your Credits page. OpenRouter changes that allowance.

1. Sign up at https://openrouter.ai/
2. Open https://openrouter.ai/keys
3. Create a key. Name it `hydra`. Copy it. It starts with `sk-or-`. The full key is shown once.
4. In the terminal:

```text
hydra auth openrouter
```

5. Paste the key when asked, and press Enter.

If Cloudflare is also set up, `hydra free` asks Cloudflare first, then OpenRouter's free models. With only OpenRouter, `hydra free` uses the free models directly.

```text
hydra free "Give me three weeknight dinners that use pasta."
```

See the nicknames Hydra knows, including the free model list at the bottom:

```text
hydra --list-models
```

## A model on your own computer

This part is optional. Ollama runs a model on your machine. The question stays on the computer. A laptop without a graphics chip can answer, and it may be slow. That slowness is the computer working, not a broken install.

Ollama's download page: https://ollama.com/download

- Windows and Mac: download the app, install it, and open it. Leave it running. On a Mac it lives in the menu bar.
- Linux, paste:

```text
curl -fsSL https://ollama.com/install.sh | sh
```

Hydra's local command looks for a model named `qwen2.5-coder:7b`. The download is a few gigabytes. A computer with 8 GB of memory is a comfortable starting point.

```text
ollama pull qwen2.5-coder:7b
```

Then:

```text
hydra local "Write four lines about rain on a window."
```

If Linux says it cannot connect, start Ollama with `ollama serve` and leave that window open. Use a second terminal window for `hydra local`.

## Credits, in plain words

A credit is a dollar you paid to one website in advance. Each question subtracts a fraction of a cent, or several cents, depending on the model. A short question to a small model costs less than a long question to a famous one.

Each company has its own balance. Money you add at OpenRouter stays at OpenRouter. It does not fill up Vercel, Hugging Face, or Cheaper Inference.

Hydra never asks for your card. You pay on the company's website, the same way you would pay for an app. A first top-up of $5 or $10 is enough to learn what the paid models feel like. Leave automatic top-up off until you know how fast you spend. Automatic top-up charges your card again when the balance falls under a number you pick.

Inside a Hydra conversation, this asks the companies you have connected and prints what they report:

```text
/credits
```

You type that after you have started the conversation in the next section. It also answers to `/budgets`, `/usage`, and `/endpoints`.

## Paid account: OpenRouter credits

Use this when you want models beyond the free list, or when the daily free cap runs out.

1. Sign in at https://openrouter.ai/
2. Open the Credits page: https://openrouter.ai/settings/credits
3. Add credits. OpenRouter's own FAQ describes a 5.5% fee on credit purchases, with a minimum fee. The checkout page shows the charge for that day. Read it before you confirm.
4. Leave auto top-up off for the first week.
5. The key you already saved keeps working. A new balance does not need a new key.

Then a paid question looks like this. The default model is `mimo 2.6 pro`.

```text
hydra "Explain, in everyday words, what an API key is."
```

A specific model spends at that model's price:

```text
hydra sonnet 5.5 "Rewrite this sentence so it sounds kinder: You are late again."
```

The Activity page in the OpenRouter account lists what each question cost.

## Paid account: Vercel AI Gateway

Vercel is another switchboard, with its own balance and its own key.

1. Create an account at https://vercel.com/signup
2. Open the AI Gateway area in the dashboard.
3. Open the API keys page and create a key. Copy it immediately. Vercel will not show it again. Their steps: https://vercel.com/docs/ai-gateway/getting-started
4. In the terminal:

```text
hydra auth vercel
```

5. Paste the key when it asks for `AI_GATEWAY_API_KEY`.

Vercel gives every team a free tier. The free credit starts the first time you send a request. The free tier includes some models, and it allows fewer requests per minute than the paid tier. Their pricing page is the source of truth: https://vercel.com/docs/ai-gateway/pricing

Vercel's launch notes describe that monthly credit as $5 every 30 days. The balance in the dashboard is the number to trust, because they can change the amount.

The first time you buy credits, the account becomes a paid account and the monthly free credit stops. Read that sentence again before you pay. If the free credit is enough for now, leave the buy button alone.

To add money later:

1. Open AI Gateway in the dashboard.
2. Choose the balance button in the upper right.
3. Pick an amount, then **Continue to Payment**, then **Confirm and Pay**.
4. Auto top-up is off until you turn it on from that same window.

## Paid account: Cheaper Inference

Cheaper Inference sells access to many models at a discount, and it is one of the companies that can serve Hydra's default model. Their site: https://cheaperinference.com/

As of October 15, 2026, their own steps say:

1. Create an account.
2. Add at least $5 to the wallet. The first successful funding adds $10 in bonus credit. Read the checkout page in case that offer has changed.
3. Create an API key and copy it once.
4. In the terminal:

```text
hydra auth cheaperinference
```

5. Paste the key when it asks for `CHEAPERINFERENCE_API_KEY`.

Their docs: https://cheaperinference.com/docs

Usage shows up in the History page on their site, and in `/credits` inside Hydra.

## Paid account: Hugging Face

Hugging Face is a model library with a paid inference service. A free Hugging Face login is real, and it is useful for browsing models. As of October 15, 2026, their pricing page says free accounts get no monthly inference credits. Using the inference service means buying credits, or a PRO plan that includes $2 of credits a month.

Pricing: https://huggingface.co/docs/inference-providers/pricing

1. Create an account at https://huggingface.co/join
2. Open https://huggingface.co/settings/tokens
3. Create a fine-grained token that is allowed to **Make calls to Inference Providers**. Copy it.
4. If you need credits, buy them from the billing page in your Hugging Face settings. PRO is the subscription that includes the monthly $2.
5. In the terminal:

```text
hydra auth huggingface
```

6. Paste the token when it asks for `HF_TOKEN`.

Ask with:

```text
hydra hf "Summarize the idea of a to-do list in two sentences."
```

## Paid endpoints: RunPod and Modal

Skip this section if the words GPU, container, and endpoint are new. Cloudflare, OpenRouter, Vercel, and Cheaper Inference already answer questions. RunPod and Modal are for the day you want to rent a computer that runs a model you picked yourself.

The rented computer can cost money while it is turned on, including while you are away from the keyboard. Turn it off in that company's dashboard when you are finished. Hydra does not turn it off for you.

### RunPod

1. Open https://www.runpod.io/console/serverless and create an account.
2. Create a serverless endpoint for the model you want, or use a pod they already offer. Copy the API key and the endpoint id from the dashboard. The endpoint id is the short name RunPod shows for that rented model.
3. Save the key:

```text
hydra auth runpod
```

4. Save the endpoint id. The command asks for the value so it stays out of the terminal history:

```text
hydra auth set RUNPOD_ENDPOINT_ID
```

Paste the id and press Enter. If the dashboard gave you a full web address instead of an id, use this command and paste the address:

```text
hydra auth set RUNPOD_ENDPOINT_URL
```

### Modal

1. Open https://modal.com/ and create an account.
2. Deploy the OpenAI-compatible address Modal shows you. It looks like `https://something.modal.run`.
3. Save that address:

```text
hydra auth modal
```

Paste the address when asked for `MODAL_ENDPOINT_URL`. It needs to start with `https://`.

If Modal also gave you a key, save it the same hidden way:

```text
hydra auth set MODAL_API_KEY
```

## Have a conversation

One question from the terminal starts fresh every time. A conversation that remembers the last thing you said is the agent.

Typing `hydra` by itself opens a simpler window. Each sentence there is a separate question, and that window uses the paid lane. Use the `hydra agent` lines below when you want it to remember, and when you want the free lane.

Free lane, after Cloudflare or OpenRouter is saved:

```text
hydra agent --free
```

A picture of a hydra may print. That is decoration. Under it, you get a line that ends with `>` or a box you can type in. The line includes the word `free` when you are on the free lane.

Type a normal sentence. Quotes are unnecessary here.

```text
Help me write a short, polite note to my landlord about a dripping sink.
```

Press Enter. Read the answer. Ask a follow-up in the same window. It remembers this conversation.

Useful lines, typed on their own:

- `/help` shows the commands.
- `/credits` shows balances and the free allowance Hydra can see.
- `/tier` shows the lane. `/tier free` stays on the free accounts. `/tier frontier` uses the paid accounts and spends credits. `/tier local` uses Ollama.
- `/model` prints a numbered list. The short name is the nickname. The long name beside it is the technical id, and you can ignore it. `/model 4` picks item 4. `/model sonnet 5.5` picks that nickname.
- In a free session, the answer still comes from the free lane until you type `/tier frontier`. Switching the nickname does not, by itself, start spending credits.
- `/clear` starts a fresh conversation.
- `/quit` leaves. Ctrl+C twice also leaves.

Paid conversation, once a balance exists:

```text
hydra agent
```

The prompt shows `frontier` instead of `free`. That is the lane that spends credits. The usual model is `mimo 2.6 pro`.

Ask before it touches files:

```text
hydra agent --free --steer
```

`--steer` makes Hydra stop and ask before it reads or changes a file, or runs a command. You can also turn that on inside the chat with `/steer on`.

A single paid question, without staying in the chat:

```text
hydra sonnet 5.5 "What should I pack for a rainy weekend?"
```

The quotes matter on that line, because the terminal splits words. Inside `hydra agent`, you just type the sentence.

## What you can ask it to do

Start with ordinary requests. The same sentences you would type into ChatGPT work here.

- "Explain this paragraph to me as if I am new to it."
- "Turn these bullets into a kind email."
- "Give me a meal plan for two people, with a short grocery list."
- "I have a headache and a tight deadline. Help me write the first paragraph, and keep it simple."

It can look at a file on your computer when you ask. First go to the folder that holds the file. This example uses Documents. The `cd` command means "go into this folder."

Windows, Mac, and Linux:

```text
cd Documents
hydra agent --free --steer
```

Then, in the chat:

```text
Read notes.txt and tell me the three main points.
```

Because `--steer` is on, it asks before it opens the file. Approve the ones you recognize. Start Hydra in a folder you are happy for it to see. A folder of tax papers or password notes is a poor place to start.

Several models at once is called a swarm. It spends more, because several models answer and then one of them combines the answers. Try it after you have a few dollars of credits, with a short question:

```text
hydra swarm "List the tradeoffs of cooking a big pot of soup on Sunday."
```

Web search and a browser are optional extras. The conversation works without them. When you want those, a person who is comfortable installing programs can run `hydra mcp init`. You can ignore the words MCP, swarm, and gateway until then.

`/help` inside the chat is the map of the rest. You do not need to memorize it.

## When the terminal says something confusing

**`command not found` or `not recognized`.** Close the terminal, open a new one, and try again. If `python --version` or `python3 --version` also fails, Python still needs to be installed, with the Windows PATH box turned on.

**The paste into `hydra auth` seems to do nothing.** The field is hiding the key. Paste once, then press Enter. An empty Enter skips the key.

**`Hydra rejected` or `Unauthorized`.** The copy of the key is wrong, or the key was deleted on the website. Create a new key, run `hydra auth` again, and type `n` if it offers to save a key it already rejected.

**`No frontier provider credentials`.** You asked a paid model, and no paid key is saved yet. Use `hydra free "your question"`, or save an OpenRouter, Vercel, or Cheaper Inference key.

**`402`, or a line about credits.** The balance at that company is empty or below zero. Add a small top-up, or switch to the free lane with `hydra free` or `/tier free`.

**`429`, or a line about rate limits.** You have sent a lot of questions in a short time, or you have used today's free allowance. Wait a minute. If it keeps happening, the daily free cap is used up. Tomorrow's allowance, or a credit top-up, clears it.

**The cursor sits there.** A larger model thinks before it types. A minute can be normal. Ctrl+C stops the wait.

**The answer is cut off.** Ask "Please continue from where you stopped." In the agent chat, it can see what it already wrote.

**You want the free lane again.** Type `/tier free`, or leave with `/quit` and start `hydra agent --free`.

## A card you can keep

```text
hydra setup                         print this guide
hydra --version                     check that Hydra is installed
hydra auth                          save a key (paste when asked)
hydra auth status                   see which keys are saved, masked
hydra auth test openrouter          ask OpenRouter if the key works
hydra free "your question"          one free question
hydra agent --free                  a free conversation that remembers
hydra agent --free --steer          same, and it asks before touching files
hydra agent                         a paid conversation (spends credits)
hydra "your question"               one paid question, default model
hydra sonnet 5.5 "your question"    one question to that model
hydra local "your question"         answer on this computer, via Ollama
hydra --list-models                 show the nicknames
```

Inside `hydra agent`:

```text
/help       show the commands
/credits    show balances and free allowance
/tier free  stay on the free accounts
/model      pick a nickname from a numbered list
/steer on   ask before touching files
/clear      start a fresh conversation
/quit       leave
```

## For people who write programs

Embedding Hydra in Python or Node, pipes, and the local gateway are written up in `GUIDE.md` at the project root. The project page is https://github.com/erastudil/hydra
