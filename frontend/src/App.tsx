import { useEffect, useState } from "react";
import { Benchmark } from "./Benchmark";
import { WaveScreen } from "./WaveScreen";
import { parseRoute, waveHash } from "./route";

export function App() {
  const [route, setRoute] = useState(location.hash);
  const [scope, setScope] = useState("");
  const [waveId, setWaveId] = useState<string | null>(null);
  useEffect(() => {
    const on = () => setRoute(location.hash);
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  const { screen, page } = parseRoute(route);
  const bench = screen === "benchmark";
  return (
    <>
      <header className="topbar">
        <div className="bar-inner">
          <a className="brand" href="#/wave"><img src="/mark.svg" alt="" /><span>Final Approach</span></a>
          <span className="scope">{bench ? "model benchmark" : scope || "arrival wave"}</span>
          <nav className="topnav" aria-label="Screens">
            <a href={waveHash("home", waveId)} aria-current={!bench && page === "home" ? "page" : undefined}>Arrival wave</a>
            <a href={waveHash("details", waveId)} aria-current={!bench && page === "details" ? "page" : undefined}>Details</a>
            <a href="#/benchmark" aria-current={bench ? "page" : undefined}>Benchmark</a>
          </nav>
        </div>
      </header>
      {bench ? <Benchmark /> : <WaveScreen page={page} onScope={setScope} onWaveId={setWaveId} />}
    </>
  );
}
