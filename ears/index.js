// «Уши и рот» бота: держит голосовое соединение (DAVE E2EE через discord.js),
// слышит участников, режет речь на фразы и отдаёт их Python-мозгу; играет его ответы.
const http = require("http");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { Client, GatewayIntentBits, Events } = require("discord.js");
const {
  joinVoiceChannel,
  getVoiceConnection,
  createAudioPlayer,
  createAudioResource,
  entersState,
  VoiceConnectionStatus,
  AudioPlayerStatus,
  EndBehaviorType,
} = require("@discordjs/voice");
const prism = require("prism-media");

const TOKEN = process.env.DISCORD_TOKEN;
const EARS_PORT = parseInt(process.env.EARS_PORT || "8300", 10);
const BRAIN_PORT = parseInt(process.env.PORT || "10000", 10);
const EARS_TOKEN = process.env.EARS_TOKEN || "dev";
const BYTES_PER_SEC = 48000 * 2 * 2; // 48кГц стерео 16бит
const MIN_PCM_BYTES = Math.floor(BYTES_PER_SEC * 0.25);
const MAX_PCM_BYTES = BYTES_PER_SEC * 30;

const log = (...a) => console.log("[ears]", ...a);

const client = new Client({
  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates],
});

const states = new Map(); // guildId -> { player, queue, subs, current, subscribedConn }

function getState(guildId) {
  let st = states.get(guildId);
  if (!st) {
    st = { player: null, queue: [], subs: new Set(), current: null, subscribedConn: null };
    states.set(guildId, st);
  }
  return st;
}

function wavHeader(dataLen) {
  const b = Buffer.alloc(44);
  b.write("RIFF", 0);
  b.writeUInt32LE(36 + dataLen, 4);
  b.write("WAVE", 8);
  b.write("fmt ", 12);
  b.writeUInt32LE(16, 16);
  b.writeUInt16LE(1, 20);
  b.writeUInt16LE(2, 22);
  b.writeUInt32LE(48000, 24);
  b.writeUInt32LE(BYTES_PER_SEC, 28);
  b.writeUInt16LE(4, 32);
  b.writeUInt16LE(16, 34);
  b.write("data", 36);
  b.writeUInt32LE(dataLen, 40);
  return b;
}

async function postUtterance(guildId, userId, file) {
  try {
    await fetch(`http://127.0.0.1:${BRAIN_PORT}/utterance`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Ears-Token": EARS_TOKEN },
      body: JSON.stringify({ guild_id: guildId, user_id: userId, path: file }),
    });
  } catch (e) {
    log("post utterance failed:", e.message);
  }
}

function subscribeUser(conn, guildId, userId) {
  const st = getState(guildId);
  if (st.subs.has(userId)) return;
  st.subs.add(userId);

  const opus = conn.receiver.subscribe(userId, {
    end: { behavior: EndBehaviorType.AfterSilence, duration: 900 },
  });
  const dec = new prism.opus.Decoder({ rate: 48000, channels: 2, frameSize: 960 });
  const chunks = [];
  let size = 0;

  dec.on("data", (c) => {
    if (size < MAX_PCM_BYTES) {
      chunks.push(c);
      size += c.length;
    }
  });

  const finish = () => {
    st.subs.delete(userId);
    if (size < MIN_PCM_BYTES) return;
    const pcm = Buffer.concat(chunks);
    const file = path.join(os.tmpdir(), `utt_${userId}_${Date.now()}.wav`);
    fs.writeFile(file, Buffer.concat([wavHeader(pcm.length), pcm]), (err) => {
      if (err) return log("wav write failed:", err.message);
      log(`utterance ${userId}: ${(size / BYTES_PER_SEC).toFixed(1)}s`);
      postUtterance(guildId, userId, file);
    });
  };

  dec.on("end", finish);
  opus.on("error", (e) => { log("opus stream error:", e.message); finish(); });
  dec.on("error", (e) => log("decoder error:", e.message));
  opus.pipe(dec);
}

function attachReceiver(conn, guildId) {
  conn.receiver.speaking.on("start", (userId) => {
    if (userId === client.user.id) return;
    subscribeUser(conn, guildId, userId);
  });
}

async function join(guildId, channelId) {
  const guild = await client.guilds.fetch(guildId);
  const conn = joinVoiceChannel({
    channelId,
    guildId,
    adapterCreator: guild.voiceAdapterCreator,
    selfDeaf: false,
    selfMute: false,
  });
  try {
    await entersState(conn, VoiceConnectionStatus.Ready, 20_000);
  } catch (e) {
    conn.destroy();
    throw new Error("voice connect timeout");
  }
  attachReceiver(conn, guildId);
  conn.on(VoiceConnectionStatus.Disconnected, async () => {
    try {
      await Promise.race([
        entersState(conn, VoiceConnectionStatus.Signalling, 5_000),
        entersState(conn, VoiceConnectionStatus.Connecting, 5_000),
      ]);
    } catch {
      conn.destroy();
    }
  });
  log("joined", guildId, channelId);
}

function leave(guildId) {
  const conn = getVoiceConnection(guildId);
  if (conn) conn.destroy();
  const st = getState(guildId);
  st.queue = [];
  if (st.player) st.player.stop();
  st.subscribedConn = null;
  log("left", guildId);
}

function playNext(guildId) {
  const st = getState(guildId);
  if (st.current) {
    fs.unlink(st.current, () => {});
    st.current = null;
  }
  const file = st.queue.shift();
  if (!file) return;
  st.current = file;
  st.player.play(createAudioResource(file));
}

function play(guildId, file) {
  const conn = getVoiceConnection(guildId);
  if (!conn) throw new Error("not connected to voice");
  const st = getState(guildId);
  if (!st.player) {
    st.player = createAudioPlayer();
    st.player.on(AudioPlayerStatus.Idle, () => playNext(guildId));
    st.player.on("error", (e) => {
      log("player error:", e.message);
      playNext(guildId);
    });
  }
  if (st.subscribedConn !== conn) {
    conn.subscribe(st.player);
    st.subscribedConn = conn;
  }
  st.queue.push(file);
  if (st.player.state.status === AudioPlayerStatus.Idle && !st.current) playNext(guildId);
}

const server = http.createServer((req, res) => {
  if (req.method === "GET") {
    res.end(`ears ok ready=${client.isReady()}`);
    return;
  }
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", async () => {
    try {
      if (!client.isReady()) throw new Error("discord client not ready");
      const data = body ? JSON.parse(body) : {};
      if (req.url === "/join") await join(String(data.guild_id), String(data.channel_id));
      else if (req.url === "/leave") leave(String(data.guild_id));
      else if (req.url === "/play") play(String(data.guild_id), data.path);
      else {
        res.statusCode = 404;
        res.end("nope");
        return;
      }
      res.end("ok");
    } catch (e) {
      log("http", req.url, "error:", e.message);
      res.statusCode = 500;
      res.end(e.message);
    }
  });
});

client.once(Events.ClientReady, () => {
  log(`logged in as ${client.user.tag}`);
  server.listen(EARS_PORT, "127.0.0.1", () => log(`http on 127.0.0.1:${EARS_PORT}`));
});

client.login(TOKEN);
