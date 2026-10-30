# System Architecture Diagrams

Mermaid diagrams (render on GitHub). Solid boxes exist today; dashed boxes are planned.

## 1. Whole system

```mermaid
flowchart TB
    subgraph DATA["Data layer (implemented v0)"]
        SRC[Licensed sources<br/>SOURCE.json gate] --> FILT[Quality filter<br/>structure + lint]
        SYN[Synthetic factory<br/>verifier-gated] --> FILT
        FILT --> DEDUP[Exact + MinHash dedup] --> SPLIT[Hash split<br/>train / val] --> MAN[(manifest.json<br/>lineage)]
    end
    subgraph MODEL["Model layer (implemented, nano scale)"]
        TOK[Byte-level BPE] --> NET[Transformer<br/>RMSNorm · RoPE · GQA · SwiGLU]
        NET --- MOE[Sparse MoE layers]
        NET --- REC[Recurrent depth]
    end
    subgraph TRAIN["Training"]
        PT[Pretraining] --> SFT[SFT]:::planned --> RL[RL w/ verifiable rewards]:::planned
    end
    subgraph VERIFY["Verifiers (core asset)"]
        LEX[Luau lexer] --> CHK[Structure checker] --> LINT[Security lint GM001-007]
        LINT -.-> TYPES[Type checker]:::planned -.-> RUN[Runtime + API mocks]:::planned
    end
    subgraph EVAL["Evaluation"]
        GRIB[GRIB v0 static] --> CONT[Contamination control]
        COMP[Competitor adapters]:::planned
    end
    subgraph RESEARCH["Research lab"]
        REG[(Experiment registry)] --> GATE[Promotion gate]
    end
    subgraph PRODUCT["Product (planned)"]
        INF[Inference server]:::planned --> AGENTS[Agent supervisor]:::planned
        MEM[Project memory]:::planned --> AGENTS
        AGENTS --> STUDIO[Studio plugin bridge]:::planned
        AGENTS --> IDE[Desktop IDE]:::planned
    end
    SPLIT --> TOK
    MODEL --> TRAIN
    VERIFY --> FILT
    VERIFY --> GRIB
    VERIFY -.-> RL
    TRAIN --> EVAL --> RESEARCH
    GATE -.->|human-approved promotion| INF
    classDef planned stroke-dasharray: 5 5
```

## 2. Continuous improvement loop

```mermaid
flowchart LR
    E[Evaluate production model<br/>GRIB + regression suite] --> W[Find weaknesses<br/>per-category failures]
    W --> H[Hypothesis]
    H --> X[Experiment design<br/>baseline, >=3 seeds]
    X --> D[Collect / synthesize data<br/>verifier-gated]
    D --> T[Train candidate<br/>isolated cluster]
    T --> G{Promotion gate<br/>primary > noise<br/>no guard regressions}
    G -- fail --> R[(Record negative result)]
    G -- pass --> A{Human approval}
    A -- approve --> P[Promote; keep previous version]
    A -- reject --> R
    P --> E
    R --> W
```

## 3. Model block

```mermaid
flowchart TB
    IN[token ids] --> EMB[Embedding]
    EMB --> B1
    subgraph LOOP["x n_loops (shared weights)"]
        B1[Block 1: RMSNorm → GQA+RoPE → residual → RMSNorm → SwiGLU → residual]
        B1 --> B2[Block 2: ... → MoE router → top-k experts → residual]
        B2 --> BN[...]
    end
    BN --> NORM[RMSNorm] --> HEAD[LM head, tied to embedding] --> OUT[logits]
```

## 4. Studio integration (planned)

```mermaid
sequenceDiagram
    participant U as Developer
    participant A as GODMODE agent
    participant S as Desktop service
    participant P as Studio plugin
    U->>A: "Add a validated shop remote"
    A->>S: plan + patch
    S->>P: apply_patch(preview=true)
    P-->>S: diff of DataModel changes
    S-->>U: preview + verifier results
    U->>S: approve
    S->>P: apply_patch(preview=false) inside ChangeHistoryService recording
    P-->>S: result (success / error)
    S-->>A: confirmed state (never assumed)
```

## 5. Infrastructure scaling path

```mermaid
flowchart LR
    S0[S0: CPU<br/>nano, CI] --> S1[S1: 1 GPU<br/>bf16, compile] --> S2[S2: 8 GPU node<br/>FSDP] --> S3[S3: multi-node<br/>FSDP+TP+PP+EP] --> S4[S4: dedicated clusters<br/>pretrain / RL / eval / inference / assets]
```
