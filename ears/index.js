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

const states = new Map(); // guildId -> состояние гильдии

function getState(guildId) {
  let st = states.get(guildId);
  if (!st) {
    st = {
      subs: new Set(),
      speechPlayer: null,
      speechQueue: [],
      currentSpeech: null,
      musicPlayer: null,
      musicQueue: [],
      musicActive: false,
      subscription: null,
      subscribedTo: null,
    };
    states.set(guildId, st);
  }
  return st;
}

function subscribeTo(guildId, which) {
  const st = getState(guildId);
  const conn = getVoiceConnection(guildId);
  if (!conn) return;
  const player = which === "music" ? st.musicPlayer : st.speechPlayer;
  if (!player || st.subscribedTo === which) return;
  if (st.subscription) st.subscription.unsubscribe();
  st.subscription = conn.subscribe(player);
  st.subscribedTo = which;
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
  st.speechQueue = [];
  st.musicQueue = [];
  st.musicActive = false;
  if (st.speechPlayer) st.speechPlayer.stop();
  if (st.musicPlayer) st.musicPlayer.stop();
  st.subscription = null;
  st.subscribedTo = null;
  log("left", guildId);
}

function ensurePlayers(guildId) {
  const st = getState(guildId);
  if (!st.speechPlayer) {
    st.speechPlayer = createAudioPlayer();
    st.speechPlayer.on(AudioPlayerStatus.Idle, () => nextSpeech(guildId));
    st.speechPlayer.on("error", (e) => {
      log("speech player error:", e.message);
      nextSpeech(guildId);
    });
  }
  if (!st.musicPlayer) {
    st.musicPlayer = createAudioPlayer();
    st.musicPlayer.on(AudioPlayerStatus.Idle, () => nextTrack(guildId));
    st.musicPlayer.on("error", (e) => {
      log("music player error:", e.message);
      nextTrack(guildId);
    });
  }
}

// --- речь: приоритетнее музыки, музыка на паузу ---

function nextSpeech(guildId) {
  const st = getState(guildId);
  if (st.currentSpeech) {
    fs.unlink(st.currentSpeech, () => {});
    st.currentSpeech = null;
  }
  const file = st.speechQueue.shift();
  if (file) {
    st.currentSpeech = file;
    st.speechPlayer.play(createAudioResource(file));
    return;
  }
  // речь кончилась — возвращаем музыку
  if (st.musicActive) {
    subscribeTo(guildId, "music");
    st.musicPlayer.unpause();
  }
}

function play(guildId, file) {
  if (!getVoiceConnection(guildId)) throw new Error("not connected to voice");
  const st = getState(guildId);
  ensurePlayers(guildId);
  st.speechQueue.push(file);
  if (st.musicActive) st.musicPlayer.pause();
  subscribeTo(guildId, "speech");
  if (st.speechPlayer.state.status === AudioPlayerStatus.Idle && !st.currentSpeech) {
    nextSpeech(guildId);
  }
}

// --- музыка ---

function nextTrack(guildId) {
  const st = getState(guildId);
  const track = st.musicQueue.shift();
  if (!track) {
    st.musicActive = false;
    return;
  }
  st.musicActive = true;
  st.musicPlayer.play(createAudioResource(track.url));
  log("music:", track.title);
  if (!st.currentSpeech && st.speechQueue.length === 0) subscribeTo(guildId, "music");
}

function music(guildId, url, title) {
  if (!getVoiceConnection(guildId)) throw new Error("not connected to voice");
  const st = getState(guildId);
  ensurePlayers(guildId);
  st.musicQueue.push({ url, title });
  if (!st.musicActive) nextTrack(guildId);
}

function skip(guildId) {
  const st = getState(guildId);
  if (st.musicPlayer) st.musicPlayer.stop(); // Idle → nextTrack
}

function stopMusic(guildId) {
  const st = getState(guildId);
  st.musicQueue = [];
  st.musicActive = false;
  if (st.musicPlayer) st.musicPlayer.stop();
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
      else if (req.url === "/music") music(String(data.guild_id), data.url, data.title);
      else if (req.url === "/skip") skip(String(data.guild_id));
      else if (req.url === "/stopmusic") stopMusic(String(data.guild_id));
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
