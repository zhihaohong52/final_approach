import { useEffect, useState } from "react";
import { Benchmark } from "./Benchmark";
import { WaveScreen } from "./WaveScreen";

export function App() {
  const [route, setRoute] = useState(location.hash);
  const [scope, setScope] = useState("");
  useEffect(() => {
    const on = () => setRoute(location.hash);
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  const bench = route.startsWith("#/benchmark");
  return (
    <>
      <header className="topbar">
        <div className="bar-inner">
          <a className="brand" href="#/wave"><img src="/mark.svg" alt="" /><span>Final Approach</span></a>
          <span className="scope">{bench ? "model benchmark" : scope || "arrival wave"}</span>
          <nav className="topnav" aria-label="Screens">
            <a href="#/wave" aria-current={!bench ? "page" : undefined}>Arrival wave</a>
            <a href="#/benchmark" aria-current={bench ? "page" : undefined}>Benchmark</a>
          </nav>
        </div>
      </header>
      {bench ? <Benchmark /> : <WaveScreen onScope={setScope} />}
    </>
  );
}
