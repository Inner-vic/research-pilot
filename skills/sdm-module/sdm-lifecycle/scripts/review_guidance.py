"""Research reasoning guidance shared by fresh offline and fixed-chat reviews."""

PROMPT_VERSION = 'sdm-research-review/v6'


def research_guidance_v2():
    return (
        '\nResearch procedure (apply only the parts relevant to this task):\n'
        'First identify the question and the claim these observations can support: '
        'route/runtime feasibility, performance under a fixed recipe, attainable performance '
        'under a declared tuning budget, or candidate confirmation. Do not convert one claim into another. '
        'A ten-update runtime probe cannot reject an architecture or establish model quality.\n'
        'For a scientific comparison, distinguish the factor being studied, interacting settings '
        'that need tuning, and fixed conditions with justified limitations. A module can need a '
        'different learning rate or regularization. If separate tuning is unaffordable, explicitly '
        'limit the conclusion to the fixed recipe; do not silently retune or discard the direction.\n'
        'Inspect all relevant trials, failures and training/validation curves before choosing a winner. '
        'Check whether the best point is at a search boundary, sampling is too sparse, failures '
        'concentrate in a region, or apparent improvement comes from checkpoint selection. '
        'An unstable upper learning-rate boundary is not evidence to expand blindly. '
        'Distinguish a broken pipeline from an infeasible scientific configuration.\n'
        'State competing explanations and the smallest observation that separates them. '
        'Explain what you would do for each main outcome. No score gain can still yield useful '
        'knowledge; a better score alone does not establish a mechanism. When an ablation loses, '
        'distinguish component dependence under this recipe from necessity after fair retraining.\n'
        'Before extending training, compare matched actual optimizer progress, evaluation support '
        'and critical slices; inspect warmup/decay, initialization and resume state. Short-budget '
        'ranking is provisional. Ask whether the next useful action is to broaden candidate '
        'exploration at the present duration, adjust the search, repeat a comparison, diagnose, '
        'or increase duration. Longer training is not the default response to every gain.\n'
        'Separate checkpoint-to-checkpoint noise, retraining variation, finite/grouped evaluation '
        'support and hyperparameter-selection variation. Choose evidence collection that addresses '
        'the decisive source. Repeating only the winning seed/configuration or bootstrapping fixed '
        'predictions cannot measure every source. State unmeasured uncertainty and condition the claim.\n'
        'Recommend adoption only when the supported benefit justifies added compute, latency, '
        'memory and maintenance under project constraints. Separate a provisional research baseline '
        'from final qualification and retain the original Base.\n'
        'If decisive evidence is missing, return readable questions with stable IDs, the required '
        'evidence, its decision impact and a feasible collection method; do not guess values or '
        'force a final decision. Questions are discussion, not decision JSON and not executable commands. '
        'The outer agent retains them and follows the existing evidence-supplement workflow.\n'
        'Only when ready for a final decision use the attached BMAD JSON contract. Its one-variable '
        'rule constrains an executable successor, not the research hypotheses or discussion. '
        'A study needing several trials or coupled settings remains a proposal: use a grounded '
        'stop-analysis rather than encoding it as one compound variable. Never execute code, '
        'training, extra trials or resource changes from this review.\n'
    )


def research_guidance_v3():
    return (research_guidance_v2() +
        '\nContext alignment precedes recommendations: the human and coding agent may have '
        'agreed constraints and rejected options that are not in the result metrics. Read the '
        'shared task context: background, user agreement with its source, original/current baseline, '
        'accepted plan and rationale, changed/fixed settings, relevant failed or rejected attempts, '
        'evidence index, unknowns, current authority and the precise decision requested. Restate '
        'your understanding and raise discrepancies before selecting a remedy. Missing context '
        'requires questions, not invented assumptions. Quality of the plan and deliverable takes '
        'precedence over an immediate final answer; additional dialogue turns do not authorize ML.\n'
        'Pro is an optional external reviewer. In local-agent mode the active coding agent performs '
        'the same context, evidence, counterargument and decision checks without any Pro session, '
        'transport, or fabricated external acknowledgement. Record reviewer provenance explicitly.\n')


def iteration_guidance_v4(purpose='result'):
    """Shared research outcome, not a solver, scoring policy or execution grant."""
    if purpose not in ('plan', 'result'):
        raise ValueError('explicit_planning_purpose_required')
    return (
        'Produce a useful ' + ('initial study' if purpose == 'plan' else 'successor decision') +
        ' for an executor who does not have this conversation. Optimize progress toward the user\'s '
        'actual objective and decision-relevant learning, not the number of experiments or a filled template. '
        'Restore goal priorities, accepted constraints, baseline/history, earlier predictions and rejected '
        'directions. An unmet target is an observation, not a diagnosis.\n'
        'Separate what ran, what was measured, what the model achieved, and what is safe to execute next. '
        'Valid partial training can support a bounded next-step proposal even if the platform task failed '
        'or the requested final epoch was not reached. Missing resume state blocks a resume, not analysis '
        'or every fresh-initialization proposal. Preserve mandatory execution/cleanup gates separately.\n'
        'For each claim cite exact evidence, its scope and contrary evidence; do not confuse metric '
        'definition/support/denominator changes with learning, validation support with training coverage, '
        'or correlation with mechanism. Do not replace the original metric or baseline after seeing results. '
        'Uncertainty should limit the claim; ask only for missing facts that could change the choice.\n'
        'Choose the next action by the unresolved question it can settle. Consider plausible competing '
        'explanations, including measurement, data/labels, optimization, generalization and objective '
        'mismatch only where relevant. Prefer existing evidence or a direct repair when decisive; do not '
        'force a diagnostic experiment for a known defect. A large gap does not by itself justify lowering '
        'LR, more epochs, more cards, a new module or arbitrary loss reweighting.\n'
        'Recommend the smallest informative, feasible intervention. Explain why it is preferable to '
        'material alternatives and what result would change that recommendation. Identify the studied '
        'factor, necessary interacting settings, fixed conditions and confounders; reuse trustworthy '
        'controls with their limitations, retain failed trials, and avoid repeating a rejected setting '
        'unless new evidence makes it informative. Do not force a fixed candidate/round/seed count.\n'
        'When proposing computation, state the starting state, actual parameter/schedule change, matched '
        'comparison window and observations separating support, rejection, a tradeoff, inconclusiveness '
        'and invalid execution. Goals/thresholds and tolerances come from project requirements or measured '
        'variation, not plausible numbers. Metric gain and useful information are separate outcomes; '
        'no gain may still resolve a hypothesis, while an invalid comparison cannot reject it.\n'
        'End with the selected action, evidence-based reason and outcome-dependent next choices. '
        'Keep proposal quality, model quality and execution readiness distinct. Respect the existing '
        'single-variable successor wire contract; a coupled study needs its own proposal, not a compound '
        'variable. Do not execute, fabricate authority or call a proposed next round completed.\n'
    )


def research_guidance_v4():
    return (iteration_guidance_v4() +
        'For search/ablation claims distinguish fixed-recipe effects from attainable performance under '
        'fair tuning. Check search boundaries, feasibility and selection bias before ranking candidates. '
        'Account for checkpoint, retraining, grouped evaluation and search uncertainty separately; '
        'extra seeds or bootstrap do not answer every uncertainty. Adoption must justify compute, '
        'serving and maintenance cost under the project constraints.\n'
        'Align the human and coding Agent context before external review: background, sourced agreement, '
        'original/current baseline, accepted plan and rationale, changed/fixed settings, relevant history, '
        'evidence index, unknowns, authority and the exact requested decision. Pro is optional; in '
        'local-agent mode the active Agent performs the same evidence and counterargument checks, '
        'without a Pro session or fabricated external acknowledgement.\n'
        'If decisive context is missing, return focused questions with stable IDs, decision impact and '
        'a feasible collection method. Do not force final JSON before discussion is ready. Final '
        'responses follow the attached contract; a decision never grants execution authority.\n')


def iteration_guidance_v5(purpose='result'):
    return iteration_guidance_v4(purpose) + (
        'Before expensive queued submissions, separate the proposer and critic roles. The critic must '
        'challenge the hypothesis, evidence and counterevidence, matched comparison, material alternatives, '
        'queue/compute cost, feasibility and stop/recovery paths. The proposer must answer and revise '
        'with traceable challenge IDs; revisit the exact final proposal before dispatch. Do not rubber-stamp '
        'an empty extra turn, pretend role separation proves independent model calls, or equate an informative '
        'negative result with wasted computation. Follow the project-owned bounded dialogue contract.\n'
        'Pro configuration is optional, but once a project binds Pro it is mandatory for proposal dialogue '
        'and result-driven decisions. Use the same verified fixed conversation for multiple substantive '
        'proposal turns and each result review. Missing delivery/response/provenance means wait; never '
        'silently substitute local-agent or count a local answer as a Pro turn. Review grants no execution authority.\n')


def research_guidance_v5():
    old=research_guidance_v4()
    return old.replace(iteration_guidance_v4(),iteration_guidance_v5()).replace(
        'Pro is optional; in local-agent mode the active Agent performs the same evidence and counterargument checks, ',
        'Configuring Pro is optional; local-agent mode is allowed only when Pro is not configured, with the same evidence and counterargument checks, ')


def iteration_guidance(purpose='result'):
    return iteration_guidance_v4(purpose) + (
        'Reassess task-model fit before treating the selected baseline as the only research space. '
        'Identify the task-relevant structure, required information, invariances, resolution and constraints; '
        'trace the actual path from observations through representation, feature acquisition/alignment, '
        'fusion and prediction to the optimized objective and reported outcome. When the task or representation '
        'changes, ask which assumptions of the old architecture or module interfaces no longer hold. '
        'Distinguish a demonstrated defect, insufficient learning under this recipe, and a hypothesized '
        'representation/information/inductive-bias mismatch. A short or poorly tuned run cannot establish '
        'an architectural ceiling; a strong pretrained replacement does not identify architecture as the cause. '
        'Localize the plausible bottleneck with available evidence before choosing tuning, a sampling/fusion '
        'change, objective/representation change, backbone replacement or a different model family. '
        'Do not always blame the model first, require every diagnostic category, launch a trial for each '
        'explanation, or equate a larger/global-context model with corrected spatial correspondence. '
        'Select the feasible action that discriminates the consequential alternatives; a direct repair or '
        'backbone comparison may be appropriate without a long diagnostic ladder. State what each result '
        'would support, leave uncertain, or cause you to abandon. Architectural scope changes still follow '
        'the project boundaries; do not modify a frozen baseline or silently expand authorization.\n'
        'Use actual distinct model A/B calls for proposal and critique, with shared source evidence and '
        'traceable model/call provenance. A explains the failure mechanism and why this intervention fits; '
        'B challenges both the task/model assumptions and whether the comparison can support the claim. '
        'Keep material alternatives inside the current plan; create a separate branch only when evidence '
        'requires it. Follow the selected condition-driven review policy: approve when ready, revise with '
        'substantive progress, pause for evidence or stalemate, reject an unsound proposal. No fixed turn count. '
        'Pro is an optional backend even when a binding is stored. Use it only when selected; preserve the '
        'selected transport and actual receipts, never invent calls or treat role labels as independent models. '
        'Review does not grant execution authority or delay owned-resource cleanup.\n')


def research_guidance():
    return research_guidance_v4().replace(iteration_guidance_v4(),iteration_guidance()).replace(
        'Pro is optional; in local-agent mode the active Agent performs the same evidence and counterargument checks, ',
        'Pro is optional; in model-handoff mode preserve actual distinct-model calls and evidence checks, ')


def bind_iteration_guidance(request, previous=None, purpose='result'):
    """Pin fresh requests; replay old requests without rewriting their prompts."""
    metadata = {'planning_prompt_version', 'planning_guidance'}
    if previous is not None:
        if {k:v for k,v in previous.items() if k not in metadata} != request:
            raise ValueError('immutable_planning_request_changed')
        if metadata.intersection(previous):
            guidance={'sdm-research-review/v4':iteration_guidance_v4,'sdm-research-review/v5':iteration_guidance_v5,
                      PROMPT_VERSION:iteration_guidance}.get(previous.get('planning_prompt_version'))
            if guidance is None:
                raise ValueError('unsupported_planning_prompt_version')
            if previous.get('planning_guidance') != guidance(purpose):
                raise ValueError('planning_guidance_integrity_failed')
        return previous
    return {**request, 'planning_prompt_version': PROMPT_VERSION,
            'planning_guidance': iteration_guidance(purpose)}
