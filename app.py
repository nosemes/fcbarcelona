import csv
import random
import unicodedata
import asyncio
import io
import os
from pathlib import Path

import edge_tts
from flask import Flask, render_template_string, jsonify, request, session, send_file

APP_TITLE = "Quiz Barça · Lectoescriptura"
CSV_FILENAME = "jugadors_barcelona.csv"
VOICE = "ca-ES-EnricNeural"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "barca-lectoescriptura-clau-local")


def normalize_text(text: str) -> str:
    text = text.strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("’", "'").replace("–", "-")
    return " ".join(text.split())


def load_data():
    csv_path = Path(__file__).with_name(CSV_FILENAME)
    if not csv_path.exists():
        raise FileNotFoundError(f"No s'ha trobat {CSV_FILENAME}.")

    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    rows = None
    for enc in encodings:
        try:
            with csv_path.open("r", encoding=enc, newline="") as f:
                rows = list(csv.reader(f, delimiter=";"))
            break
        except UnicodeDecodeError:
            continue

    if not rows:
        raise RuntimeError("No s'ha pogut llegir el CSV.")

    temporades = {}
    for row in rows:
        if not row:
            continue
        inici = row[0].strip()
        if not inici.isdigit():
            continue

        any_inici = int(inici)
        jugadors = []
        for valor in row[1:]:
            nom = valor.strip()
            if nom and nom not in jugadors:
                jugadors.append(nom)

        if len(jugadors) >= 4:
            temporades[any_inici] = jugadors

    if not temporades:
        raise RuntimeError("No hi ha temporades amb almenys 4 jugadors.")
    return temporades


TEMPORADES = load_data()


def etiqueta_temporada(any_inici):
    return f"{any_inici}-{any_inici + 1}"


def choose_round():
    any_inici = random.choice(list(TEMPORADES.keys()))
    jugadors = TEMPORADES[any_inici]

    # TOTS QUATRE jugadors són de la mateixa temporada.
    opcions = random.sample(jugadors, 4)
    correcte = random.choice(opcions)
    random.shuffle(opcions)
    return any_inici, correcte, opcions


async def generar_audio(text):
    communicate = edge_tts.Communicate(text=text, voice=VOICE)
    dades = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            dades.extend(chunk["data"])
    return bytes(dades)


HTML = """
<!DOCTYPE html>
<html lang="ca">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }}</title>
<style>
* { box-sizing: border-box; }
body { margin:0; font-family:Arial,sans-serif; background:#f2f7fb; color:#1f3555; }
.container { max-width:1100px; min-height:100vh; margin:auto; padding:18px; display:flex; flex-direction:column; }
h1 { margin:0; font-size:clamp(26px,4vw,38px); }
#status { margin-top:8px; font-size:20px; color:#314d6e; }
.topbar { display:flex; justify-content:space-between; align-items:center; gap:15px; flex-wrap:wrap; margin:18px 0 12px; }
#score { font-size:22px; font-weight:bold; color:#0a5a2a; }
.controls { display:flex; gap:10px; flex-wrap:wrap; }
button { border:2px solid #cbd5e1; border-radius:12px; cursor:pointer; font-family:Arial,sans-serif; }
.control { padding:12px 18px; font-size:18px; font-weight:bold; }
#repeatButton { background:#dbeafe; }
#newButton { background:#dcfce7; }
.season { text-align:center; font-size:clamp(24px,4vw,34px); font-weight:bold; margin:6px 0 14px; color:#1f3555; }
.options { display:grid; grid-template-columns:1fr 1fr; gap:18px; flex:1; }
.option { min-height:145px; padding:18px; background:white; font-size:clamp(22px,3vw,32px); font-weight:bold; color:#172033; box-shadow:0 2px 6px rgba(0,0,0,.08); }
.option:hover:not(:disabled) { background:#fff7d6; }
.option:disabled { cursor:default; opacity:1; }
.correct { background:#22c55e !important; color:#052e16; }
.wrong { background:#ef4444 !important; color:white; }
.answer { margin-top:20px; }
.answer-title { font-size:20px; font-weight:bold; margin-bottom:8px; }
.answer-row { display:flex; gap:10px; }
#answerInput { flex:1; min-width:0; font-size:25px; padding:12px; border:2px solid #94a3b8; border-radius:10px; }
#checkButton { padding:12px 20px; font-size:19px; font-weight:bold; background:#fde68a; }
#feedback { min-height:30px; margin-top:12px; font-size:20px; font-weight:bold; }
@media (max-width:700px) {
    .options { grid-template-columns:1fr; }
    .option { min-height:90px; }
    .answer-row { flex-direction:column; }
}
</style>
</head>
<body>
<div class="container">
<h1>FC Barcelona · joc de lectoescriptura</h1>
<div id="status">Escolta la temporada i el jugador. Clica el nom correcte.</div>

<div class="topbar">
    <div id="score">Punts: 0 &nbsp; | &nbsp; ⭐ 0 &nbsp; | &nbsp; Ronda: 0</div>
    <div class="controls">
        <button id="repeatButton" class="control" onclick="repeatVoice()">🔊 Repetir veu</button>
        <button id="newButton" class="control" onclick="newRound()">Nova ronda</button>
    </div>
</div>

<div id="season" class="season">Temporada</div>
<div id="options" class="options"></div>

<div class="answer">
    <div class="answer-title">Si l'encertes, escriu el nom del jugador aquí:</div>
    <div class="answer-row">
        <input id="answerInput" type="text" disabled autocomplete="off">
        <button id="checkButton" onclick="checkWrittenAnswer()" disabled>Comprovar</button>
    </div>
    <div id="feedback"></div>
</div>
</div>

<script>
let currentSeason = "";
let currentPlayer = "";
let currentOptions = [];
let currentSelectionCorrect = false;
let currentAudio = null;
let currentAudioUrl = null;

function updateScore(score, rounds) {
    const stars = score > 0 ? "⭐".repeat(score) : "0";
    document.getElementById("score").innerHTML =
        `Punts: ${score} &nbsp; | &nbsp; ⭐ ${stars} &nbsp; | &nbsp; Ronda: ${rounds}`;
}

async function speak(text) {
    try {
        if (currentAudio) {
            currentAudio.pause();
            currentAudio = null;
        }
        if (currentAudioUrl) {
            URL.revokeObjectURL(currentAudioUrl);
            currentAudioUrl = null;
        }

        const response = await fetch("/audio", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({text:text})
        });
        if (!response.ok) throw new Error("Error generant audio");

        const blob = await response.blob();
        currentAudioUrl = URL.createObjectURL(blob);
        currentAudio = new Audio(currentAudioUrl);

        currentAudio.onended = () => {
            if (currentAudioUrl) URL.revokeObjectURL(currentAudioUrl);
            currentAudioUrl = null;
            currentAudio = null;
        };

        await currentAudio.play();
    } catch (error) {
        console.error(error);
        document.getElementById("status").innerText =
            "Prem 🔊 Repetir veu per escoltar la temporada i el jugador.";
    }
}

function voiceText() {
    return `Temporada ${currentSeason}. ${currentPlayer}.`;
}

async function newRound() {
    const response = await fetch("/nova_ronda", {method:"POST"});
    const data = await response.json();

    currentSeason = data.season_voice;
    currentPlayer = data.player;
    currentOptions = data.options;
    currentSelectionCorrect = false;

    document.getElementById("season").innerText = `Temporada ${data.season_label}`;
    document.getElementById("status").innerText =
        "Escolta la temporada i el jugador. Clica el nom correcte.";

    const feedback = document.getElementById("feedback");
    feedback.innerText = "";
    feedback.style.color = "#7c2d12";

    const input = document.getElementById("answerInput");
    input.value = "";
    input.disabled = true;
    document.getElementById("checkButton").disabled = true;

    renderOptions();
    updateScore(data.score, data.rounds);
    speak(voiceText());
}

function renderOptions() {
    const container = document.getElementById("options");
    container.innerHTML = "";

    currentOptions.forEach((player,index) => {
        const button = document.createElement("button");
        button.className = "option";
        button.innerText = player;
        button.onclick = () => chooseOption(index);
        container.appendChild(button);
    });
}

function chooseOption(index) {
    if (currentSelectionCorrect) return;

    const buttons = document.querySelectorAll(".option");
    const chosen = currentOptions[index];
    const isCorrect = chosen === currentPlayer;

    buttons.forEach(button => button.disabled = true);

    if (isCorrect) {
        currentSelectionCorrect = true;
        buttons[index].classList.add("correct");

        document.getElementById("status").innerText =
            "Molt bé. Ara escriu el nom del jugador.";

        const feedback = document.getElementById("feedback");
        feedback.innerText = "Correcte. Ara escriu el nom del jugador.";
        feedback.style.color = "#0a5a2a";

        const input = document.getElementById("answerInput");
        input.disabled = false;
        document.getElementById("checkButton").disabled = false;
        input.focus();
    } else {
        buttons[index].classList.add("wrong");
        buttons.forEach(button => {
            if (button.innerText === currentPlayer) button.classList.add("correct");
        });

        document.getElementById("status").innerText =
            "No és aquest jugador. Prem Nova ronda per continuar.";

        const feedback = document.getElementById("feedback");
        feedback.innerText = `No és correcte. La resposta bona era: ${currentPlayer}`;
        feedback.style.color = "#991b1b";
    }
}

async function checkWrittenAnswer() {
    if (!currentSelectionCorrect) return;

    const input = document.getElementById("answerInput");
    const typed = input.value.trim();

    if (!typed) {
        document.getElementById("feedback").innerText =
            "Primer escriu el nom del jugador.";
        return;
    }

    const response = await fetch("/comprovar", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({answer:typed})
    });
    const data = await response.json();
    const feedback = document.getElementById("feedback");

    if (data.correct) {
        feedback.innerText = `Perfecte. +1 punt. El jugador era ${data.player}`;
        feedback.style.color = "#0a5a2a";
        input.disabled = true;
        document.getElementById("checkButton").disabled = true;
        updateScore(data.score,data.rounds);
        speak("Molt bé. Un punt.");
        setTimeout(newRound,1800);
    } else {
        feedback.innerText = `No coincideix. Has escrit: ${typed}`;
        feedback.style.color = "#991b1b";
        speak("Torna-ho a provar.");
        input.focus();
        input.select();
    }
}

function repeatVoice() {
    if (currentSeason && currentPlayer) speak(voiceText());
}

document.getElementById("answerInput").addEventListener("keydown", function(event) {
    if (event.key === "Enter") checkWrittenAnswer();
});

newRound();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML, title=APP_TITLE)


@app.route("/nova_ronda", methods=["POST"])
def nova_ronda():
    any_inici, jugador, opcions = choose_round()

    session["season"] = any_inici
    session["player"] = jugador
    session["rounds"] = session.get("rounds", 0) + 1
    session.setdefault("score", 0)

    return jsonify({
        "season_label": etiqueta_temporada(any_inici),
        "season_voice": f"{any_inici} - {any_inici + 1}",
        "player": jugador,
        "options": opcions,
        "score": session["score"],
        "rounds": session["rounds"]
    })


@app.route("/comprovar", methods=["POST"])
def comprovar():
    data = request.get_json(silent=True) or {}
    resposta = str(data.get("answer", "")).strip()
    jugador = session.get("player", "")

    correcte = bool(jugador) and normalize_text(resposta) == normalize_text(jugador)

    if correcte:
        session["score"] = session.get("score", 0) + 1

    return jsonify({
        "correct": correcte,
        "player": jugador,
        "score": session.get("score", 0),
        "rounds": session.get("rounds", 0)
    })


@app.route("/audio", methods=["POST"])
def audio():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()

    if not text:
        return jsonify({"error":"Falta el text."}),400
    if len(text) > 250:
        return jsonify({"error":"Text massa llarg."}),400

    try:
        audio_bytes = asyncio.run(generar_audio(text))
        if not audio_bytes:
            raise RuntimeError("No s'ha generat àudio.")

        return send_file(
            io.BytesIO(audio_bytes),
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name="veu.mp3"
        )
    except Exception as exc:
        print("Error TTS:",exc)
        return jsonify({"error":"No s'ha pogut generar la veu."}),500


if __name__ == "__main__":
    app.run(debug=True)
