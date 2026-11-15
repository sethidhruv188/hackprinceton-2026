import { useMemo, useState } from "react";
import { useData } from "../useData.js";
import { postJSON } from "../api.js";
import { cases } from "../format.js";
import { CheckCircle2 } from "lucide-react";
import { ErrorBox, Loading } from "../components.jsx";
import { EmptyState, Pill } from "../ui.jsx";

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
    <section className="q-item" aria-labelledby={id}>
      <div>
        <div className="meta"><span className="tag">{q.type}</span> affects about {cases(q.impact_cases)}</div>
        <h3 id={id}>{q.question}</h3>
        <p className="note" style={{ marginTop: 0 }}>{q.detail} {q.product_id && <a href={`#/product/${encodeURIComponent(q.product_id)}`} style={{ textDecoration: "underline" }}>See product</a>}</p>
      </div>
      <div style={{ display: "grid", gap: 8, alignContent: "start" }}>
        <div className="choices" role="group" aria-label="Your answer">
          {CHOICES.map(([v, label]) => (
            <button key={v} type="button" className="btn" aria-pressed={answer === v} onClick={() => save(v)}>{label}</button>
          ))}
        </div>
        <label className="field-label" style={{ display: "grid", gap: 4 }}>Note (optional)
          <textarea value={note} onChange={(e) => { setNote(e.target.value); setState(answer ? "unsaved" : ""); }}
            onBlur={() => answer && state === "unsaved" && save(answer)} />
        </label>
        <span role="status" className={state === "error" ? "st-red small" : "saved"}>
          {state === "saving" ? "Saving…" : state === "saved" ? <Pill tone="ok" icon={CheckCircle2}>Saved</Pill> : state === "error" ? "Could not save. Try again." : state === "unsaved" ? "Note not saved yet" : ""}
        </span>
      </div>
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
      <div className="page-head">
        <div>
          <h2>Questions for you</h2>
          <p>Your answers make the plan better. They are saved on this computer only. Biggest questions first.</p>
        </div>
        <div className="panel-side">
          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Kind">
            <option value="all">All kinds ({data.questions.length})</option>
            {Object.entries(data.counts).map(([t, n]) => <option key={t} value={t}>{t} ({n})</option>)}
          </select>
          <div className="seg" role="group" aria-label="Show">
            {[["open", "Not answered"], ["answered", `Answered (${data.answered})`], ["all", "All"]].map(([v, l]) => (
              <button key={v} type="button" aria-pressed={open === v} onClick={() => setOpen(v)}>{l}</button>
            ))}
          </div>
          <span className="small muted" role="status">{shown.length} shown</span>
        </div>
      </div>
      {shown.length === 0 ? <div className="panel"><EmptyState icon={CheckCircle2} title="All caught up">No questions match these filters.</EmptyState></div>
        : <div className="q-list stagger">{shown.map((q) => <QuestionCard key={q.id} q={q} />)}</div>}
    </div>
  );
}
