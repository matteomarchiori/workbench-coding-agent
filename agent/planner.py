import json
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.models.provider import ModelProvider
from agent.schemas.tasks import TaskPlan
import posixpath


def validate_plan_paths(plan: TaskPlan) -> None:
    path_tools = {
        "read_workspace_file",
        "write_workspace_file",
    }

    for criterion in plan.criteria:
        path = criterion.required_path

        criterion_keys = [
            (criterion.required_tool, criterion.required_path)
            for criterion in plan.criteria
        ]

        if len(criterion_keys) != len(set(criterion_keys)):
            raise ValueError("Duplicate tool and path criteria")

        if criterion.required_tool in path_tools:
            if not path:
                raise ValueError(f"Missing required path: {criterion.criterion_id}")

            if len(path) > 500:
                raise ValueError("Required path exceeds 500 characters")

            if path.startswith("/") or "\\" in path:
                raise ValueError(f"Invalid required path: {path}")

            normalized = posixpath.normpath(path)

            if normalized in (".", "..") or normalized.startswith("../"):
                raise ValueError(f"Invalid workspace path: {path}")

        elif path is not None:
            raise ValueError(
                f"Unexpected required_path for tool "
                f"{criterion.required_tool}: {path}"
            )


ALLOWED_TOOLS = {
    "list_workspace_files",
    "read_workspace_file",
    "search_workspace_code",
    "write_workspace_file",
}
SANDBOX_TOOL = "run_workspace_checks"

PLANNER_SYSTEM_PROMPT = """
Sei il Planner di un coding agent locale.

Il tuo unico compito è trasformare la richiesta dell'utente
in un piano di lavoro strutturato.

Non eseguire operazioni.
Non inventare risultati.
Non dichiarare attività già completate.

Restituisci esclusivamente un oggetto JSON con questa struttura:

{
  "criteria": [
    {
      "criterion_id": "identificatore_univoco",
      "description": "Descrizione del requisito",
      "required_tool": "nome_strumento",
      "required_path": null
    }
  ]
}

STRUMENTI DISPONIBILI:

- list_workspace_files:
  Elenca i file del workspace.

- read_workspace_file:
  Legge il contenuto di un file.
  Richiede required_path con il percorso relativo del file.

- search_workspace_code:
  Cerca una stringa nel codice.

- write_workspace_file:
  Crea o modifica un file nel workspace.
  Richiede required_path con il percorso relativo del file.
  Può essere usato esclusivamente quando l'utente
  richiede esplicitamente una modifica.

{{SANDBOX_TOOL_INSTRUCTIONS}}

REGOLE:

1. Crea un criterio per ogni risultato operativo richiesto.
2. Non creare criteri per operazioni non richieste.
3. Usa esclusivamente gli strumenti disponibili.
4. Per leggere un file, specifica sempre required_path.
5. Usa percorsi relativi al workspace.
6. Gli identificatori dei criteri devono essere univoci.
7. Non aggiungere testo fuori dal JSON.
8. Non proporre modifiche ai file se l'utente
   chiede soltanto analisi, spiegazioni o lettura.
9. Per ogni modifica richiesta, specifica il percorso
   del file da modificare.
10. Usa run_workspace_checks soltanto se l'utente chiede
    esplicitamente di eseguire test o controlli.
"""

REVIEWER_SYSTEM_PROMPT = """
Sei il Reviewer di un coding agent locale.

Devi verificare se un piano di lavoro soddisfa integralmente
la richiesta originale dell'utente.

Riceverai:
- la richiesta originale;
- il piano proposto.

Controlla che:
1. Ogni risultato operativo richiesto sia rappresentato.
2. Non manchino operazioni necessarie.
3. Gli strumenti scelti siano coerenti con le operazioni.
4. I percorsi dei file siano corretti quando specificati.
5. Non siano presenti operazioni non richieste.
6. write_workspace_file sia presente soltanto quando
   l'utente richiede esplicitamente una modifica.
7. Ogni operazione di scrittura abbia un percorso preciso.
8. run_workspace_checks sia incluso soltanto quando l'utente
   chiede esplicitamente di eseguire test o controlli.

Restituisci esclusivamente JSON:

{
  "approved": true,
  "issues": []
}

Se il piano è incompleto:

{
  "approved": false,
  "issues": [
    "Descrizione del requisito mancante"
  ]
}

Non inventare informazioni.
Non approvare piani incompleti.
"""


class PlanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    issues: list[str] = Field(default_factory=list, max_length=10)


class TaskPlanner:
    def __init__(
        self,
        provider: ModelProvider,
        sandbox_enabled: bool = False,
    ):
        self.provider = provider
        self.allowed_tools = set(ALLOWED_TOOLS)
        sandbox_instructions = ""

        if sandbox_enabled:
            self.allowed_tools.add(SANDBOX_TOOL)
            sandbox_instructions = (
                "- run_workspace_checks:\n"
                "  Esegue pytest senza argomenti e un controllo statico "
                "Python nel sandbox isolato.\n"
                "  Non accetta comandi personalizzati; richiede required_path null."
            )
        else:
            sandbox_instructions = (
                "run_workspace_checks non è disponibile in questa istanza."
            )

        self.system_prompt = PLANNER_SYSTEM_PROMPT.replace(
            "{{SANDBOX_TOOL_INSTRUCTIONS}}",
            sandbox_instructions,
        )

    async def review_plan(
        self,
        goal: str,
        plan: TaskPlan,
    ) -> PlanReview:
        result = await self.provider.complete(
            messages=[
                {
                    "role": "system",
                    "content": REVIEWER_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": (
                        f"RICHIESTA ORIGINALE:\n{goal}\n\n"
                        f"PIANO PROPOSTO:\n"
                        f"{json.dumps(plan.model_dump(), ensure_ascii=False, indent=2)}"
                    ),
                },
            ],
            tools=None,
        )

        content = result["message"].get("content")

        if not isinstance(content, str) or not content.strip():
            raise ValueError("Reviewer returned empty content")

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("Reviewer returned invalid JSON") from exc

        return PlanReview.model_validate(data)

    async def create_plan(self, goal: str) -> TaskPlan:
        max_attempts = 3  # Piano iniziale + 2 revisioni
        feedback = None

        for attempt in range(max_attempts):
            user_content = goal

            if feedback:
                user_content += (
                    "\n\nREVISIONE DEL PIANO PRECEDENTE:\n"
                    + "\n".join(f"- {issue}" for issue in feedback)
                    + "\n\nCorreggi il piano tenendo conto di queste indicazioni."
                )

            result = await self.provider.complete(
                messages=[
                    {
                        "role": "system",
                        "content": self.system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_content,
                    },
                ],
                tools=None,
            )

            message = result["message"]
            content = message.get("content")

            if not isinstance(content, str) or not content.strip():
                raise ValueError("Planner returned empty content")

            try:
                data = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError("Planner returned invalid JSON") from exc

            plan = TaskPlan.model_validate(data)

            for criterion in plan.criteria:
                if criterion.required_tool not in self.allowed_tools:
                    raise ValueError(
                        f"Unauthorized tool in plan: " f"{criterion.required_tool}"
                    )

                if (
                    criterion.required_tool == "read_workspace_file"
                    and not criterion.required_path
                ):
                    raise ValueError("File reading criterion requires a path")

            validate_plan_paths(plan)

            review = await self.review_plan(goal, plan)

            if review.approved and not review.issues:
                return plan

            feedback = review.issues or [
                "Il piano non è stato approvato. "
                "Rivedi la copertura della richiesta originale."
            ]

        raise ValueError(
            "Planner could not produce an approved plan " "after 2 revisions"
        )
