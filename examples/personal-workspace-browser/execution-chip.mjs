import { resolve } from "node:path";

import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// The shipped steward default is one endpoint on every machine: the interactive
// CLI endpoint, which is billed to an individual CLI login and keeps the vendor
// model default. The binding names the one decision behind it, so a reader does
// not have to guess whether an endpoint was chosen or merely discovered.
const shippedDefaultBinding = {
  schema_version: "manager_channel_binding_v0",
  executor_endpoint: "codex",
  executor_endpoint_source: "product_default",
  executor_endpoint_default_reason: "steward_channel_default",
  executor_kind: "individual",
  model: "gpt-6-astra",
  model_source: "vendor_default",
  credential_env_var: "",
  operator_credential_configured: false,
  available: null,
  unavailable_reason: null,
};

// A configured operator credential authenticates a configuration; it does not
// select one. The chip must therefore be indistinguishable from the shipped
// default it did not move -- only the reported fact changes.
const credentialWithoutSelectionBinding = {
  ...shippedDefaultBinding,
  operator_credential_configured: true,
};

// Selecting the managed host is what puts the channel on it, and the model and
// reasoning effort follow that endpoint rather than the environment.
const selectedManagedBinding = {
  ...shippedDefaultBinding,
  executor_endpoint: "dsh",
  executor_endpoint_source: "explicit_config",
  executor_endpoint_default_reason: "",
  executor_kind: "managed",
  model: "deepseek-v4-flash",
  model_source: "managed_execution_profile",
  credential_env_var: "DEEPSEEK_API_KEY",
  operator_credential_configured: true,
  available: true,
  unavailable_reason: null,
  output_token_budget: {
    schema_version: "dsh_output_token_budget_v0",
    scope: "per_model_request",
    max_tokens: 16384,
    valid: true,
    source: "product_default",
    final_response_reserve_supported: false,
    hard_tool_budget_supported: false,
  },
};

// The channel can select the managed host, but if it cannot launch here the chip
// must name the missing fact instead of the transport gap it used to have.
const explicitManagedCredentialBinding = {
  ...selectedManagedBinding,
  operator_credential_configured: false,
  available: false,
  unavailable_reason: "operator_credential_unconfigured",
};

const explicitManagedRuntimeBinding = {
  ...explicitManagedCredentialBinding,
  unavailable_reason: "dsh_runtime_unavailable",
};

// An endpoint the control plane reports no kind for must not be labelled as a
// kind the operator can act on, and an unrecognized model source must not be
// reported as the vendor default.
const unknownKindBinding = {
  ...shippedDefaultBinding,
  executor_endpoint: "pi",
  executor_endpoint_source: "explicit_config",
  executor_endpoint_default_reason: "",
  executor_kind: "",
  model_source: "unrecognized_source",
};

async function openChip(browser, url, binding, collectCoverage) {
  const context = await openWorkspacePage(browser, url, {
    apiOptions: { managerChannelBinding: binding },
    collectCoverage,
  });
  try {
    await context.page.locator(".personal-execution-chip").waitFor({ state: "visible" });
    return context;
  } catch (error) {
    await context.page.screenshot({
      animations: "disabled",
      fullPage: false,
      path: resolve(outputDir, "execution-chip-failed.png"),
    });
    await context.close();
    throw error;
  }
}

async function chipText(page) {
  return (await page.locator(".personal-execution-chip").innerText()).replace(/\s+/g, " ").trim();
}

const pickerSelector = "div.personal-agent-select button.personal-select-trigger";

async function readPickerLabel(page) {
  return (await page.locator(pickerSelector).innerText()).replace(/\s+/g, " ").trim();
}

// What the page resolved, phrased so a failed CI run is diagnosable without a
// local reproduction: the declared endpoint, the adapters the page actually
// received and the label it rendered are the three facts that decide the label.
async function pickerResolution(page) {
  const capabilities = await page.evaluate(async () => {
    try {
      const response = await fetch("/api/chat/capabilities");
      const body = await response.json();
      return {
        status: response.status,
        declaredEndpoint: body.manager?.channel_binding?.executor_endpoint ?? null,
        adapters: (body.adapters ?? []).map(
          (adapter) => `${adapter.agent_id}:${adapter.available ? "available" : "unavailable"}`,
        ),
      };
    } catch (error) {
      return { status: "unavailable", declaredEndpoint: null, adapters: [], error: String(error) };
    }
  });
  return [
    `label=${await readPickerLabel(page)}`,
    `declared=${capabilities.declaredEndpoint ?? "<none>"}`,
    `adapters=[${capabilities.adapters.join(", ")}]`,
    `capabilities=${capabilities.status}${capabilities.error ? ` (${capabilities.error})` : ""}`,
  ].join(" ");
}

// The picker resolves from the same capabilities response that carries the
// channel binding, and the execution chip only renders once that binding
// lands. A single read therefore asserts on a state the page never promised was
// settled, and it can still hold the pre-fetch Codex fallback. Wait for the
// declared value instead, and name what the page really resolved if the wait
// runs out.
async function waitForPickerLabel(page, settled, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  let label = await readPickerLabel(page);
  while (!settled(label) && Date.now() < deadline) {
    await page.waitForTimeout(100);
    label = await readPickerLabel(page);
  }
  if (settled(label)) {
    return label;
  }
  const resolution = await pickerResolution(page);
  await page.screenshot({
    animations: "disabled",
    fullPage: false,
    path: resolve(outputDir, "execution-chip-picker-unresolved.png"),
  });
  throw new Error(`Chat runtime picker never settled: ${resolution}`);
}

async function assertHairlineRow(page, maxHeight = 26) {
  const headerBox = await page.locator(".personal-channel-header").boundingBox();
  const chipBox = await page.locator(".personal-execution-chip").boundingBox();
  if (!headerBox || !chipBox) throw new Error("Execution chip has no layout box");
  if (chipBox.y < headerBox.y || chipBox.y + chipBox.height > headerBox.y + headerBox.height) {
    throw new Error("Execution chip escaped the channel header row");
  }
  if (chipBox.height > maxHeight) {
    throw new Error(`Execution chip is not a compact hairline row: ${chipBox.height}px tall`);
  }
}

export const executionChipScenario = {
  id: "execution-chip",
  async run({ browser, collectCoverage, url }) {
    const shipped = await openChip(browser, url, shippedDefaultBinding, collectCoverage);
    let shippedText = "";
    let shippedDefaultNote = "";
    const { close, coverageEntries, page } = shipped;
    try {
      const text = await chipText(page);
      shippedText = text;
      for (const fragment of ["codex", "gpt-6-astra", "个人 CLI 登录"]) {
        if (!text.includes(fragment)) {
          throw new Error(`Execution chip omitted ${fragment}: ${text}`);
        }
      }
      if (text.includes("deepseek")) {
        throw new Error(`Selecting the CLI endpoint must not move the model: ${text}`);
      }
      if (await page.locator(".personal-execution-note").count() !== 0) {
        throw new Error(`A usable steward channel must not render an unavailability note: ${await chipText(page)}`);
      }
      // The default is one endpoint, so the chip names it and the one way to
      // move it, and never claims a credential branch that does not exist.
      await page.locator(".personal-runtime-details > summary").click();
      const defaultNote = await page.locator(".personal-execution-rule-note").innerText();
      shippedDefaultNote = defaultNote;
      if (!defaultNote.includes("出货默认值") || !defaultNote.includes("codex")) {
        throw new Error(`Shipped default did not explain itself: ${defaultNote}`);
      }
      if (defaultNote.includes("operator 凭据")) {
        throw new Error(`The shipped default claimed a credential branch: ${defaultNote}`);
      }
      await assertHairlineRow(page);
      await page.screenshot({
        animations: "disabled",
        fullPage: false,
        path: resolve(outputDir, "steward-execution-chip-desktop.png"),
      });
    } finally {
      await close();
    }

    // A configured operator credential reports a fact and moves nothing: this
    // regression is the whole point, so compare the two chips literally.
    const credentialOnly = await openChip(
      browser,
      url,
      credentialWithoutSelectionBinding,
      collectCoverage,
    );
    try {
      const text = await chipText(credentialOnly.page);
      if (text !== shippedText) {
        throw new Error(
          `A configured credential moved the steward chip: ${shippedText} -> ${text}`,
        );
      }
      await credentialOnly.page.locator(".personal-runtime-details > summary").click();
      const credentialDefaultNote = await credentialOnly.page
        .locator(".personal-execution-rule-note")
        .innerText();
      if (credentialDefaultNote !== shippedDefaultNote) {
        throw new Error("A configured credential changed the shipped-default explanation");
      }
    } finally {
      coverageEntries.push(...await credentialOnly.close());
    }

    // Selecting the managed host moves the endpoint, the model and the effort
    // together, and an explicit selection carries no shipped-default note.
    const managed = await openChip(browser, url, selectedManagedBinding, collectCoverage);
    try {
      const text = await chipText(managed.page);
      for (const fragment of ["dsh", "operator 凭据", "deepseek-v4-flash", "每次请求 16,384 token"]) {
        if (!text.includes(fragment)) {
          throw new Error(`Managed host chip omitted ${fragment}: ${text}`);
        }
      }
      if (await managed.page.locator(".personal-execution-note").count() !== 0) {
        throw new Error("A launchable managed host must not render an unavailability note");
      }
      if (await managed.page.locator(".personal-execution-rule-note").count() !== 0) {
        throw new Error("A selected endpoint must not be explained as a shipped default");
      }
      if (text.includes("codex")) {
        throw new Error(`The selected managed host must not still report the CLI endpoint: ${text}`);
      }
      await assertHairlineRow(managed.page);
      await managed.page.screenshot({ animations: "disabled", fullPage: false,
        path: resolve(outputDir, "execution-chip-dsh-budget-desktop.png") });
      await managed.page.setViewportSize({ width: 390, height: 844 });
      await assertHairlineRow(managed.page, 44);
      const budget = managed.page.locator(".personal-execution-chip-budget");
      if (!await budget.isVisible()) throw new Error("Mobile header lost the output budget");
      const box = await budget.boundingBox();
      if (!box || box.x < 0 || box.x + box.width > 390) throw new Error("Mobile output budget is clipped");
      await managed.page.screenshot({ animations: "disabled", fullPage: false,
        path: resolve(outputDir, "execution-chip-dsh-budget-mobile.png") });
    } finally {
      coverageEntries.push(...await managed.close());
    }

    for (const invalid of [false, true]) {
      const binding = invalid ? {
        ...selectedManagedBinding, available: false,
        unavailable_reason: "invalid_output_token_limit",
        output_token_budget: { ...selectedManagedBinding.output_token_budget, max_tokens: null, valid: false },
      } : selectedManagedBinding;
      const english = await openWorkspacePage(browser, url, {
        apiOptions: { managerChannelBinding: binding }, collectCoverage,
        beforeGoto: async (_api, page) => page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "en")),
      });
      try {
        await english.page.locator(".personal-execution-chip").waitFor({ state: "visible" });
        const content = invalid
          ? await english.page.locator(".personal-execution-note").innerText()
          : await chipText(english.page);
        if (!content.includes(invalid ? "positive integer" : "16,384 tok/request")) {
          throw new Error(`English budget readback is missing: ${content}`);
        }
      } finally { coverageEntries.push(...await english.close()); }
    }

    // The chat-runtime picker has to report the same resolution as the chip: a
    // managed host declared for this machine is what will answer, so neither
    // the header trigger nor the composer may keep advertising the CLI the
    // machine merely happens to have installed.
    const managedHostAdapters = [{
      adapter_kind: "deepseek_harness_segment",
      agent_id: "dsh",
      available: true,
      display_name: "DeepSeek Harness (managed)",
      interrupt: false,
      resume: true,
      streaming: false,
    }];
    const stewardPicker = await openWorkspacePage(browser, url, {
      apiOptions: { managerChannelBinding: selectedManagedBinding, runtimeAgents: managedHostAdapters },
      collectCoverage,
    });
    try {
      const pickerLabel = await waitForPickerLabel(
        stewardPicker.page,
        (label) => label.includes("DeepSeek Harness (managed)"),
      );
      if (pickerLabel.includes("Codex")) {
        throw new Error(`Chat runtime picker advertised a discovered CLI as the steward: ${pickerLabel}`);
      }
      if (await stewardPicker.page.locator(".personal-channel-composer > span").count()) {
        throw new Error("Composer repeated the executor already identified by the runtime picker");
      }
    } finally {
      coverageEntries.push(...await stewardPicker.close());
    }

    // A machine that declares no steward executor keeps the shipped default,
    // so the parity above is a resolution the control plane asked for and not
    // a new hard-coded preference.
    const undeclaredSteward = await openWorkspacePage(browser, url, {
      apiOptions: { runtimeAgents: managedHostAdapters },
      collectCoverage,
    });
    try {
      // The shipped default is the settled value a machine with no declared
      // steward executor must keep.
      await waitForPickerLabel(undeclaredSteward.page, (label) => label === "Chat Codex");
    } finally {
      coverageEntries.push(...await undeclaredSteward.close());
    }

    // A managed host that cannot launch here names the missing fact, and never
    // claims the channel lacks a transport it now has.
    for (const [binding, expected] of [
      [explicitManagedCredentialBinding, "需要 operator 凭据"],
      [explicitManagedRuntimeBinding, "未安装其 runtime"],
    ]) {
      const unavailable = await openChip(browser, url, binding, collectCoverage);
      try {
        const text = await chipText(unavailable.page);
        if (!text.includes("dsh")) {
          throw new Error(`Unavailable managed chip lost its endpoint: ${text}`);
        }
        const noteText = (await unavailable.page.locator(".personal-execution-note").innerText()).replace(/\s+/g, " ").trim();
        if (!noteText.includes(expected)) {
          throw new Error(`Unavailable managed chip did not name ${expected}: ${noteText}`);
        }
        if (noteText.includes("尚无 Chat 通道") || noteText.includes("loopx turn")) {
          throw new Error(`Retired transport wording is still rendered: ${noteText}`);
        }
        if (await unavailable.page.locator(".personal-execution-chip.is-unavailable").count() !== 1) {
          throw new Error("An unavailable steward executor did not mark its chip as unavailable");
        }
        await assertHairlineRow(unavailable.page);
        if (binding === explicitManagedCredentialBinding) {
          // The documentation assets are viewport captures of exactly these
          // states, so they are produced here instead of being hand-cropped once.
          await unavailable.page.screenshot({
            animations: "disabled",
            fullPage: false,
            path: resolve(outputDir, "steward-execution-chip-unavailable-desktop.png"),
          });
        }
      } finally {
        coverageEntries.push(...await unavailable.close());
      }
    }

    // An endpoint with no reported kind, and a model source this build does not
    // recognize, must both stay visibly unclaimed rather than invent a fact.
    const unknown = await openChip(browser, url, unknownKindBinding, collectCoverage);
    try {
      const text = await chipText(unknown.page);
      if (!text.includes("pi") || !text.includes("注册端点")) {
        throw new Error(`Execution chip omitted the selected endpoint: ${text}`);
      }
      for (const invented of ["个人 CLI 登录", "operator 凭据"]) {
        if (text.includes(invented)) {
          throw new Error(`Unknown executor kind was reported as ${invented}: ${text}`);
        }
      }
      await assertHairlineRow(unknown.page);
    } finally {
      coverageEntries.push(...await unknown.close());
    }

    // A control plane that projects no binding keeps the previous header.
    const withoutBinding = await openWorkspacePage(browser, url, { collectCoverage });
    try {
      if (await withoutBinding.page.locator(".personal-execution-chip").count() !== 0) {
        throw new Error("Execution chip rendered without a projected channel binding");
      }
    } finally {
      coverageEntries.push(...await withoutBinding.close());
    }

    // The narrow header hides its subtitle row, so the chip may be out of view
    // there; if it is shown it must still fit inside the viewport, and an
    // unavailable executor must keep its reason readable instead of collapsing.
    const mobile = await openWorkspacePage(browser, url, {
      apiOptions: { managerChannelBinding: shippedDefaultBinding },
      collectCoverage,
      isMobile: true,
      viewport: { width: 390, height: 844 },
    });
    try {
      const mobileChip = mobile.page.locator(".personal-execution-chip");
      if (await mobileChip.isVisible()) {
        const chipBox = await mobileChip.boundingBox();
        const viewport = mobile.page.viewportSize();
        if (!chipBox || !viewport || chipBox.x + chipBox.width > viewport.width) {
          throw new Error("Execution chip overflows the mobile viewport");
        }
      }
    } finally {
      coverageEntries.push(...await mobile.close());
    }

    const mobileUnavailable = await openWorkspacePage(browser, url, {
      apiOptions: { managerChannelBinding: explicitManagedCredentialBinding },
      collectCoverage,
      isMobile: true,
      viewport: { width: 390, height: 844 },
    });
    try {
      const note = mobileUnavailable.page.locator(".personal-execution-note");
      await note.waitFor({ state: "visible" });
      const noteBox = await note.boundingBox();
      if (!noteBox || noteBox.width < 40) {
        throw new Error(`Unavailable transport reason collapsed in the narrow header: ${JSON.stringify(noteBox)}`);
      }
      await mobileUnavailable.page.screenshot({
        animations: "disabled",
        fullPage: false,
        path: resolve(outputDir, "steward-execution-chip-unavailable-mobile.png"),
      });
    } finally {
      coverageEntries.push(...await mobileUnavailable.close());
    }
    return {
      coverageEntries,
      note: "execution chip reports the selected executor, the credential it is billed to and the resolved model, names the steward channel's shipped default without claiming a credential branch, keeps a configured credential from moving it, keeps the chat-runtime picker on the executor the machine declares for the steward, names why a selected managed host cannot launch, and stays absent without a binding",
    };
  },
};
