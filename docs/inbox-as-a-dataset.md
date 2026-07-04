# Your inbox is a dataset you already have the rights to

I keep a personal collection of a few hundred small physical collectibles. Over a
couple of years I bought them one or two at a time from a long tail of tiny online
shops. At some point I wanted the obvious thing: a catalog. What is each item,
what did I pay, when did it ship, which shop did it come from, and where is the
receipt if I ever want to sell one. None of that lived anywhere. It was smeared
across a few hundred order emails I had already received and never thought about
again.

The instinct most people reach for first is to scrape the shops: point a scraper
at the product pages, pull the title and the photo, done. I tried that and it is
wrong on two counts. The data is wrong. A keyword search against a marketplace
returns whatever it thinks matches that text, not the specific item I bought; in
one test, searching a shop's handle on a third-party scraper returned unrelated
products that happened to share a word. And it is ethically murky to scrape
someone's catalog to reconstruct your own purchase history when you already hold a
cleaner record. You just have to notice you're holding it.

## The reframe

A purchase confirmation is self-generated ground truth. When a shop emails me
"thanks for your order" or "your stuff shipped," that message is a timestamped
record of a real transaction I was party to: the item's name, the price the seller
actually charged, the date, the shop, the order number, and — the part that makes
it searchable — a product photo the seller chose. I did not scrape that. The shop
sent it to me. The provenance is clean because the data was always mine.

So the project stopped being "scrape the web" and became "parse my own mailbox."
That single move fixes the data-quality problem and the ethics problem at the same
time, which is usually a sign you've found the right framing.

## The system, briefly

The pipeline is small and boring on purpose. Read-only IMAP pulls candidate emails
by sender and date. The mailbox is opened with `EXAMINE`, never `SELECT`, so the
engine cannot delete, move, or flag anything even by accident. Per-seller templates
parse each message into structured rows: name, price, currency, seller, order
number, date, and the URL of the product image. Emails that no template
understands are skipped by default; only if I opt in does an optional, cached small
language model take a pass at the raw text, and it caches the result so it never
re-sends. Every product image is downloaded at ingest, content-addressed by
sha256, because the links rot. Then a local CLIP model embeds each image, and
lookup is a cosine similarity over those local vectors: I hand it a photo of a
thing, it tells me which catalogued item it is.

The privacy posture is the spine, not a footnote. Secrets are never written to
disk. The IMAP password is read at runtime from the environment or the system
keychain and registered with a log redactor so it can't leak into a log line.
Every writing command is dry-run by default and does nothing until I pass
`--apply`. All image understanding stays on the machine; zero images and zero
query vectors leave it. The only thing that can ever touch a cloud model is the
*text* of an email I already received, and only when I turn it on.

## What I learned

These are observations from one real deployment — my own private collection of
257 items, of which 225 have a downloaded photo and are embedded, drawn from 74
source emails across roughly 53 shops. This is not a benchmark. It is what one
person saw running the thing for real. I'm flagging measured versus anecdotal as I
go.

**Coverage beats cleverness.** This is the finding I'd keep if I could keep only
one. When photo lookup "failed," I'd assume the search was dumb and reach for a
better query trick. Almost every time, the real problem was that the item simply
wasn't in the index — a gift with no purchase email, a convention buy, a dead
image link. The model was fine. Getting the item into the database with any real
photo mattered far more than any retrieval cleverness. Measured, in the sense that
I checked the misses one by one and they were coverage gaps, not ranking bugs.

**A bigger embedding model earned its cost.** I started on CLIP ViT-B-32 and it
confused items that share a visual style — same silhouette, same palette, different
thing. Upgrading to ViT-L-14 fixed the cases I cared about; the items that used to
collide now rank first on real photos. The catch worth writing down: ViT-L scores
run about 0.10 to 0.15 lower than ViT-B on the same matches, so the confidence
bands needed recalibrating (I moved "high confidence" to ≥0.74 and "medium" to
≥0.64). And the tricks I expected to help — center-crop, test-time augmentation —
measurably did not. The model swap was the real fix; the cleverness was noise.

**Local-first has a quantified price, and it's basically zero.** Template parsing,
image embedding, and both photo and text lookup cost $0 because they run locally.
The only metered operation is the optional language-model fallback for emails no
template handles, and that runs about $0.001 to $0.003 per email, cached so each
email is paid for at most once. So the privacy-preserving default is also the cheap
default. You only spend money, and only send text off-device, on the long tail of
weird layouts, and only if you choose to.

**The parsing gotchas were where the real work hid.** A few that cost me time, in
case they save you some:

- *Payment-processor emails have no items.* A receipt from a payment intermediary
  gives you a price and a merchant string and nothing else: no product photo, just
  the processor's logo. The real images live in the shop's own order email. If you
  treat the processor receipt as the source of truth, every row is image-less.
- *The shop's name is rarely its merchant string.* The name a shop bills under
  routinely differs from the name it sells under — accents, abbreviations,
  outright rebrands. Matching on the billing name alone silently drops orders. I
  ended up keeping an alias map.
- *Recommendation carousels are a trap.* "You might also like" blocks use the exact
  same image markup as real line items. Parse the whole email naively and you
  invent purchases that never happened — phantom items the person never bought. The
  fix is to read only the order region and cap to the email's own item count, and
  to distrust the promotional "it's on the way!" emails entirely, because they
  append recommendations.
- *The shop logo sits on the same CDN as the product photos.* Match "any image from
  that CDN" and every item's photo is off by one. You have to match the specific
  product-image markup.
- *Image links expire.* Thumbnail URLs 404, and shops quietly delete images over
  time. Download at ingest and store by content hash, or your catalog slowly goes
  blank.

## Ethics and dual-use

I'll say the obvious thing plainly: a tool that reads a mailbox and indexes its
contents is, pointed at someone else's mailbox, surveillance. The scope is the
ethical line. This runs on your own receipts, reads only, scrapes no one, and the
image lookup answers "what is this thing I already own?" — it is not a reverse
image search of the open web and identifies no people. The local-first, read-only,
secrets-off-disk, dry-run-by-default design isn't decoration. It's the part that
makes the tool one you'd be comfortable running on yourself, which is the only
mailbox it's for.

## Close

The domain — what counts as a thing worth cataloguing — is a plugin. The engine
knows how to read an email, find a price, pick a product image, store it, embed it,
and search it; it knows nothing about what you collect. To catalog something new
you write one profile file and never touch the engine. The engine, a fully
synthetic board-game demo that runs with no mailbox and no network, and a reusable
email-extraction skill are all open source in this repo. The most interesting data
you have about the physical things you own is sitting in receipts you already
received. You just have to read your own mail.
