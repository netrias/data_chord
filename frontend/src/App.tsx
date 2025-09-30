import { useState } from "react";

import {
  StepExport,
  StepHarmonize,
  StepMappings,
  StepReview,
  StepUpload
} from "./workflows";

const steps = [
  { key: "upload", label: "Upload", component: <StepUpload /> },
  { key: "mappings", label: "Review Mappings", component: <StepMappings /> },
  { key: "harmonize", label: "Harmonize", component: <StepHarmonize /> },
  { key: "review", label: "Review Results", component: <StepReview /> },
  { key: "export", label: "Export", component: <StepExport /> }
];

const App = () => {
  const [activeStep, setActiveStep] = useState(steps[0].key);

  return (
    <div style={{ maxWidth: "960px", margin: "0 auto", padding: "2rem" }}>
      <header>
        <h1>Data Harmonization Workflow (Skeleton)</h1>
        <nav style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", margin: "1.5rem 0" }}>
          {steps.map((step) => (
            <button
              key={step.key}
              onClick={() => setActiveStep(step.key)}
              style={{
                padding: "0.5rem 1rem",
                borderRadius: "9999px",
                border: "1px solid #d1d5db",
                backgroundColor: step.key === activeStep ? "#2563eb" : "#ffffff",
                color: step.key === activeStep ? "#ffffff" : "#1f2937",
                cursor: "pointer"
              }}
            >
              {step.label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {steps.find((step) => step.key === activeStep)?.component}
      </main>
    </div>
  );
};

export default App;
