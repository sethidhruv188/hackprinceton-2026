import { useMemo, useState } from "react";
import { useData } from "../useData.js";
import { postJSON } from "../api.js";
import { cases } from "../format.js";
import { ErrorBox, Loading } from "../components.jsx";

const CHOICES = [["yes", "Yes"], ["no", "No"], ["not sure", "Not sure"]];

function QuestionCard({ q }) {
  const [answer, setAnswer] = useState(q.answer);
  const [note, setNote] = useState(q.note || "");
  const [state, setState] = useState(q.answer ? "saved" : "");
  const save = async (a) => {
    setAnswer(a);
    setState("saving");
    try {
      await postJSON("/answers", { question_id: q.id, answer: a, note });
      setState("saved");
    } catch {
      setState("error");
    }
  };
  const id = `q-${q.id.replace(/[^a-z0-9]/gi, "-")}`;
  return (
    <section className="card q" aria-labelledby={id}>
      <span className="muted">{q.type} · affects about {cases(q.impact_cases)}</span>
      <h3 id={id}>{q.question}</h3>
      <p className="muted" style={{ margin: 0 }}>{q.detail} {q.product_id && <a href={`#/product/${encodeURIComponent(q.product_id)}`}>See product</a>}</p>
      <div className="choices" role="group" aria-label="Your answer">
        {CHOICES.map(([v, label]) => (
          <button key={v} type="button" aria-pressed={answer === v} onClick={() => save(v)}>{label}</button>
        ))}
      </div>
      <label>Note (optional)
        <textarea value={note} onChange={(e) => { setNote(e.target.value); setState(answer ? "unsaved" : ""); }}
          onBlur={() => answer && state === "unsaved" && save(answer)} />
      </label>
      <span role="status" className={state === "error" ? "error" : "saved"}>
        {state === "saving" ? "Saving…" : state === "saved" ? "Saved" : state === "error" ? "Could not save. Try again." : state === "unsaved" ? "Note not saved yet" : ""}
      </span>
    </section>
  );
}

export default function Questions() {
  const { data, error, loading } = useData("/questions");
  const [type, setType] = useState("all");
  const [open, setOpen] = useState("open");
  const shown = useMemo(() => (data ? data.questions.filter((q) => (type === "all" || q.type === type)
    && (open === "all" || (open === "open" ? !q.answer : !!q.answer))) : []), [data, type, open]);
  if (error) return <ErrorBox error={error} />;
  if (loading) return <Loading />;
  return (
    <div data-ready="questions">
      <h1>Questions for you</h1>
      <p className="muted">Your answers make the plan better. They are saved on this computer only. Biggest questions first.</p>
      <div className="controls">
        <label>Kind
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="all">All ({data.questions.length})</option>
            {Object.entries(data.counts).map(([t, n]) => <option key={t} value={t}>{t} ({n})</option>)}
          </select>
        </label>
        <label>Show
          <select value={open} onChange={(e) => setOpen(e.target.value)}>
            <option value="open">Not answered yet</option>
            <option value="answered">Answered ({data.answered})</option>
            <option value="all">All</option>
          </select>
        </label>
      </div>
      <p className="muted">{shown.length} questions shown.</p>
      <div className="grid">{shown.map((q) => <QuestionCard key={q.id} q={q} />)}</div>
    </div>
  );
}
