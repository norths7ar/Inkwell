import { useEffect, useState, type FormEvent } from "react";
import { z } from "zod";
import {
  Cpu,
  Lightbulb,
  MessageSquareCode,
  PenLine,
  Plus,
  RefreshCw,
  RotateCcw,
  ScrollText,
  SearchCheck,
  Trash2,
} from "lucide-react";
import {
  Alert,
  Badge,
  Button,
  Dialog,
  EmptyState,
  Input,
  Select,
  Textarea,
} from "../components/ui";
import { errorText, remove, request } from "../lib/api";
import {
  bookSchema,
  discoveredSchema,
  modelSchema,
  presetSchema,
  promptSchema,
  protocolSchema,
  providerSchema,
  settingsSchema,
  type Book,
  type Model,
  type Provider,
} from "../lib/schema";
import "./settings.css";

type Preset = z.infer<typeof presetSchema>;
type Prompt = z.infer<typeof promptSchema>;
type GlobalSettings = z.infer<typeof settingsSchema>;
type Discovered = z.infer<typeof discoveredSchema>;
const protocols = protocolSchema.options.map((value) => ({ value, label: value }));
const modeMeta = [
  {
    value: "discuss",
    label: "讨论",
    icon: Lightbulb,
    toolsHint: "可用工具：只读章节与设定、提出设定修改建议、向作者提问",
  },
  {
    value: "write",
    label: "写作",
    icon: PenLine,
    toolsHint: "可用工具：只读章节与设定、提交正文或设定待审修改、向作者提问",
  },
  {
    value: "review",
    label: "审阅",
    icon: SearchCheck,
    toolsHint: "可用工具：仅只读核查章节目录、正文、摘要、设定与搜索",
  },
] as const;

const sections = [
  { value: "models", label: "模型与提供商", icon: Cpu },
  { value: "rules", label: "写作规则", icon: ScrollText },
  { value: "prompts", label: "模式提示词", icon: MessageSquareCode },
] as const;

export function Settings({
  open,
  onOpenChange,
  onSaved,
  activeBook,
  books,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  activeBook?: Book | null;
  books?: Book[];
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="设置" className="settings-dialog" wide>
      {open && (
        <SettingsContent
          onSaved={onSaved}
          activeBook={activeBook ?? null}
          books={books ?? (activeBook ? [activeBook] : [])}
        />
      )}
    </Dialog>
  );
}

function SettingsContent({
  onSaved,
  activeBook,
  books,
}: {
  onSaved: () => void;
  activeBook: Book | null;
  books: Book[];
}) {
  const [tab, setTab] = useState<"models" | "rules" | "prompts">("models");
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [settings, setSettings] = useState<GlobalSettings | null>(null);
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [selected, setSelected] = useState("new");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<Provider | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let disposed = false;
    Promise.all([
      request("/providers", providerSchema.array()),
      request("/models", modelSchema.array()),
      request("/provider-presets", presetSchema.array()),
      request("/settings", settingsSchema),
      request("/settings/prompts", promptSchema.array()),
    ])
      .then(([nextProviders, nextModels, nextPresets, nextSettings, nextPrompts]) => {
        if (disposed) return;
        setProviders(nextProviders);
        setModels(nextModels);
        setPresets(nextPresets);
        setSettings(nextSettings);
        setPrompts(nextPrompts);
        setSelected(nextProviders[0]?.id ?? "new");
        setLoading(false);
      })
      .catch((failure: unknown) => {
        if (!disposed) {
          setError(errorText(failure));
          setLoading(false);
        }
      });
    return () => {
      disposed = true;
    };
  }, []);

  function providerSaved(provider: Provider) {
    setProviders((items) => [...items.filter((item) => item.id !== provider.id), provider]);
    setSelected(provider.id);
    onSaved();
  }
  function modelSaved(model: Model) {
    setModels((items) => [...items.filter((item) => item.id !== model.id), model]);
    onSaved();
  }
  async function deleteProvider() {
    if (!deleting) return;
    setBusy(true);
    setError("");
    try {
      await remove(`/providers/${deleting.id}`);
      const remaining = providers.filter((item) => item.id !== deleting.id);
      setProviders(remaining);
      setModels((items) => items.filter((item) => item.provider_id !== deleting.id));
      setSelected(remaining[0]?.id ?? "new");
      setDeleting(null);
      onSaved();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }
  const provider = providers.find((item) => item.id === selected);
  const enabledModels = models.filter(
    (model) =>
      model.enabled && providers.some((item) => item.id === model.provider_id && item.enabled),
  );

  return (
    <div className="settings-layout">
      <nav className="settings-navigation" aria-label="设置分类">
        {sections.map((section) => {
          const Icon = section.icon;
          return (
            <Button
              key={section.value}
              variant="ghost"
              aria-current={tab === section.value ? "page" : undefined}
              onClick={() => setTab(section.value)}
            >
              <Icon size={16} />
              {section.label}
            </Button>
          );
        })}
      </nav>
      <div className="settings-content">
        <div className="settings-scroll">
          <Alert>{error}</Alert>
          {loading && (
            <p className="muted" role="status">
              正在读取设置…
            </p>
          )}
          {!loading && tab === "models" && (
            <div className="stack">
              {settings && (
                <DefaultModelsCard
                  initial={settings}
                  models={enabledModels}
                  onSaved={(value) => {
                    setSettings(value);
                    onSaved();
                  }}
                />
              )}
              <div className="providers-master-detail">
                <div className="provider-list-pane" aria-label="提供商列表">
                  <div className="provider-list-header">
                    <span>提供商</span>
                    <Button
                      variant="ghost"
                      className="icon-control"
                      title="新建提供商"
                      aria-label="新建提供商"
                      onClick={() => setSelected("new")}
                    >
                      <Plus size={15} />
                    </Button>
                  </div>
                  {providers.map((item) => (
                    <button
                      type="button"
                      key={item.id}
                      className={`provider-list-item ${selected === item.id ? "is-selected" : ""}`}
                      onClick={() => setSelected(item.id)}
                    >
                      <span
                        className={`provider-dot ${item.enabled ? "" : "is-disabled"}`}
                        aria-hidden="true"
                      />
                      <span>{item.name}</span>
                    </button>
                  ))}
                  {selected === "new" && (
                    <button type="button" className="provider-list-item is-selected">
                      <Plus size={14} />
                      <span>新建提供商</span>
                    </button>
                  )}
                </div>
                <div className="provider-detail-pane">
                  <ProviderEditor
                    key={selected}
                    provider={provider}
                    presets={presets}
                    onSaved={providerSaved}
                    onDelete={provider ? () => setDeleting(provider) : undefined}
                  />
                  {provider && (
                    <ModelsPanel
                      key={`models-${provider.id}`}
                      provider={provider}
                      models={models.filter((item) => item.provider_id === provider.id)}
                      onSaved={modelSaved}
                      onDeleted={(id) => {
                        setModels((items) => items.filter((item) => item.id !== id));
                        onSaved();
                      }}
                    />
                  )}
                </div>
              </div>
            </div>
          )}
          {!loading && tab === "rules" && settings && (
            <RulesSection
              settings={settings}
              activeBook={activeBook}
              books={books}
              onSettingsSaved={(value) => {
                setSettings(value);
                onSaved();
              }}
              onBookSaved={onSaved}
            />
          )}
          {!loading && tab === "prompts" && <PromptEditor initial={prompts} onSaved={onSaved} />}
        </div>
      </div>
      <Dialog
        open={deleting !== null}
        onOpenChange={(value) => {
          if (!value && !busy) setDeleting(null);
        }}
        title="删除提供商"
        description={`确定删除「${deleting?.name ?? ""}」及其下的模型配置吗？不会影响作品与对话记录。`}
      >
        <Alert>{error}</Alert>
        <div className="dialog-actions">
          <Button disabled={busy} onClick={() => setDeleting(null)}>
            取消
          </Button>
          <Button variant="danger" disabled={busy} onClick={() => void deleteProvider()}>
            确认删除
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

function DefaultModelsCard({
  initial,
  models,
  onSaved,
}: {
  initial: GlobalSettings;
  models: Model[];
  onSaved: (settings: GlobalSettings) => void;
}) {
  const [primary, setPrimary] = useState(initial.default_model_id ?? "none");
  const [background, setBackground] = useState(initial.background_model_id ?? "none");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const options = [
    { value: "none", label: "未设置" },
    ...models.map((model) => ({ value: model.id, label: model.display_name || model.model_id })),
  ];
  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const result = await request("/settings", settingsSchema, {
        method: "PATCH",
        body: {
          default_model_id: primary === "none" ? null : primary,
          background_model_id: background === "none" ? null : background,
          global_rules_markdown: initial.global_rules_markdown,
        },
      });
      onSaved(result);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setSaving(false);
    }
  }
  return (
    <form className="settings-card" onSubmit={(event) => void save(event)}>
      <div className="settings-card-header">
        <div className="settings-card-title">
          <h3>默认模型</h3>
        </div>
      </div>
      <div className="settings-defaults-bar">
        <Select label="主写作模型" value={primary} onChange={setPrimary} options={options} />
        <Select
          label="后台轻量模型"
          value={background}
          onChange={setBackground}
          options={options}
        />
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? "保存中…" : "保存默认模型"}
        </Button>
      </div>
      {((primary !== "none" && !models.some((model) => model.id === primary)) ||
        (background !== "none" && !models.some((model) => model.id === background))) && (
        <Alert>所选模型已停用或删除，请重新选择。</Alert>
      )}
      <Alert>{error}</Alert>
    </form>
  );
}

function ProviderEditor({
  provider,
  presets,
  onSaved,
  onDelete,
}: {
  provider?: Provider;
  presets: Preset[];
  onSaved: (provider: Provider) => void;
  onDelete?: () => void;
}) {
  const [name, setName] = useState(provider?.name ?? "");
  const [preset, setPreset] = useState(provider?.preset || "custom");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [discoveryUrl, setDiscoveryUrl] = useState(provider?.discovery_url ?? "");
  const [protocol, setProtocol] = useState(provider?.default_protocol ?? "openai-chat");
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);
  const [apiKey, setApiKey] = useState("");
  const [headers, setHeaders] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [clearHeaders, setClearHeaders] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  function choosePreset(value: string) {
    const chosen = presets.find((item) => item.id === value);
    setPreset(value);
    if (chosen) {
      setBaseUrl(chosen.base_url);
      setDiscoveryUrl(chosen.discovery_url);
      setProtocol(chosen.default_protocol);
      if (!name.trim()) setName(chosen.name);
    }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      let parsedHeaders: Record<string, string> | undefined;
      if (clearHeaders) parsedHeaders = {};
      else if (headers.trim()) {
        try {
          const raw: unknown = JSON.parse(headers);
          parsedHeaders = z.record(z.string(), z.string()).parse(raw);
        } catch {
          throw new Error("自定义请求头必须是 JSON 对象，且每个值都是文本。");
        }
      }
      const saved = await request(
        provider ? `/providers/${provider.id}` : "/providers",
        providerSchema,
        {
          method: provider ? "PATCH" : "POST",
          body: {
            name: name.trim(),
            preset,
            base_url: baseUrl.trim(),
            discovery_url: discoveryUrl.trim(),
            default_protocol: protocol,
            enabled,
            ...(clearKey ? { api_key: "" } : apiKey ? { api_key: apiKey } : {}),
            ...(parsedHeaders === undefined ? {} : { headers: parsedHeaders }),
          },
        },
      );
      setApiKey("");
      setHeaders("");
      setClearKey(false);
      setClearHeaders(false);
      onSaved(saved);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setSaving(false);
    }
  }
  return (
    <form className="settings-card" onSubmit={(event) => void save(event)}>
      <div className="settings-card-header">
        <div className="settings-card-title">
          <h3>{provider ? provider.name : "新建提供商"}</h3>
        </div>
        <div className="row">
          <Button
            variant="secondary"
            disabled={saving}
            onClick={() => setEnabled((value) => !value)}
          >
            {enabled ? "已启用" : "已停用"}
          </Button>
          {onDelete && (
            <Button
              variant="ghost"
              className="icon-control"
              title="删除提供商"
              aria-label="删除提供商"
              disabled={saving}
              onClick={onDelete}
            >
              <Trash2 size={15} />
            </Button>
          )}
          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? "保存中…" : "保存提供商"}
          </Button>
        </div>
      </div>
      <div className="settings-grid-3">
        <Input
          label="名称"
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          placeholder="例如：OpenAI"
        />
        <Select
          label="提供商预设"
          value={preset}
          onChange={choosePreset}
          options={presets.map((item) => ({ value: item.id, label: item.name }))}
        />
        <Select
          label="默认协议"
          value={protocol}
          onChange={(value) => setProtocol(protocolSchema.parse(value))}
          options={protocols}
        />
      </div>
      <div className="settings-grid-2-wide">
        <Input
          label="API 地址"
          type="url"
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          required
          placeholder="https://…/v1"
        />
        <Input
          label="模型发现地址（可选）"
          type="url"
          value={discoveryUrl}
          onChange={(event) => setDiscoveryUrl(event.target.value)}
          placeholder="留空则使用 API 地址 /models"
        />
      </div>
      <div className="credential-row">
        <Input
          label="API Key（留空保留现有密钥）"
          type="password"
          autoComplete="new-password"
          value={apiKey}
          disabled={clearKey}
          onChange={(event) => setApiKey(event.target.value)}
        />
        <Badge tone={provider?.api_key_configured ? "success" : "neutral"}>
          {provider?.api_key_configured ? "已配置密钥" : "未配置密钥"}
        </Badge>
        {provider?.api_key_configured && (
          <Button
            variant={clearKey ? "danger" : "secondary"}
            aria-pressed={clearKey}
            onClick={() => setClearKey((value) => !value)}
          >
            {clearKey ? "撤销清除" : "清除密钥"}
          </Button>
        )}
      </div>
      <details className="settings-advanced">
        <summary>
          高级选项：自定义请求头（{provider?.headers_configured ? "已配置" : "未配置"}）
        </summary>
        <div className="stack">
          <Textarea
            label="自定义请求头（JSON；留空保留现有配置）"
            value={headers}
            disabled={clearHeaders}
            onChange={(event) => setHeaders(event.target.value)}
            rows={3}
            placeholder={'{"X-Custom-Header": "value"}'}
            spellCheck={false}
          />
          {provider?.headers_configured && (
            <Button
              className="credential-action"
              variant={clearHeaders ? "danger" : "secondary"}
              aria-pressed={clearHeaders}
              onClick={() => setClearHeaders((value) => !value)}
            >
              {clearHeaders ? "撤销清除请求头" : "保存时清除请求头"}
            </Button>
          )}
        </div>
      </details>
      {(clearKey || clearHeaders) && <Badge tone="warning">将于保存时清除所选凭据</Badge>}
      <Alert>{error}</Alert>
    </form>
  );
}

function ModelsPanel({
  provider,
  models,
  onSaved,
  onDeleted,
}: {
  provider: Provider;
  models: Model[];
  onSaved: (model: Model) => void;
  onDeleted: (id: string) => void;
}) {
  const [editing, setEditing] = useState<Model | "new" | null>(null);
  const [deleting, setDeleting] = useState<Model | null>(null);
  const [discovered, setDiscovered] = useState<Discovered[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function discover() {
    setBusy(true);
    setError("");
    try {
      setDiscovered(
        await request(`/providers/${provider.id}/discover`, discoveredSchema.array(), {
          method: "POST",
        }),
      );
      setSelected([]);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }
  async function importModels() {
    setBusy(true);
    setError("");
    try {
      for (const model of discovered ?? []) {
        if (
          !selected.includes(model.model_id) ||
          models.some((item) => item.model_id === model.model_id)
        )
          continue;
        const saved = await request(`/providers/${provider.id}/models`, modelSchema, {
          method: "POST",
          body: { model_id: model.model_id, display_name: model.display_name },
        });
        onSaved(saved);
        setSelected((items) => items.filter((item) => item !== model.model_id));
      }
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }
  async function deleteModel() {
    if (!deleting) return;
    setBusy(true);
    setError("");
    try {
      await remove(`/models/${deleting.id}`);
      onDeleted(deleting.id);
      setDeleting(null);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="settings-card">
      <div className="settings-card-header">
        <div className="settings-card-title">
          <h3>模型目录</h3>
        </div>
        <div className="row">
          <Button onClick={() => setEditing("new")}>
            <Plus size={15} />
            手动添加
          </Button>
          <Button disabled={busy} onClick={() => void discover()}>
            <RefreshCw size={15} />
            发现模型
          </Button>
        </div>
      </div>
      <Alert>{error}</Alert>
      {models.length === 0 && <EmptyState title="暂无模型" />}
      <div className="stack">
        {models.map((model) => (
          <div className="row model-row" key={model.id}>
            <div className="model-description">
              <strong>{model.display_name || model.model_id}</strong>
              <div className="muted">{model.model_id}</div>
            </div>
            <Badge>{model.protocol_override ?? "继承提供商协议"}</Badge>
            {!model.enabled && <Badge tone="warning">已停用</Badge>}
            <Button variant="ghost" onClick={() => setEditing(model)}>
              编辑
            </Button>
            <Button
              variant="ghost"
              aria-label={`删除模型 ${model.model_id}`}
              onClick={() => setDeleting(model)}
            >
              <Trash2 size={16} />
            </Button>
          </div>
        ))}
      </div>
      {discovered !== null && (
        <section className="stack discovery-preview">
          <h4>发现预览 · {discovered.length} 个模型</h4>
          <div className="row">
            <Button
              disabled={busy}
              onClick={() =>
                setSelected(
                  discovered
                    .filter((model) => !models.some((item) => item.model_id === model.model_id))
                    .map((model) => model.model_id),
                )
              }
            >
              选中全部新模型
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => setSelected([])}>
              清空选择
            </Button>
          </div>
          <div className="discovery-list">
            {discovered.map((model) => {
              const exists = models.some((item) => item.model_id === model.model_id);
              return (
                <Button
                  key={model.model_id}
                  disabled={exists || busy}
                  aria-pressed={selected.includes(model.model_id)}
                  variant={selected.includes(model.model_id) ? "primary" : "secondary"}
                  onClick={() =>
                    setSelected((items) =>
                      items.includes(model.model_id)
                        ? items.filter((item) => item !== model.model_id)
                        : [...items, model.model_id],
                    )
                  }
                >
                  {model.display_name || model.model_id}
                  {exists ? " · 已导入" : ""}
                </Button>
              );
            })}
          </div>
          {discovered.length === 0 && <p className="muted">未发现模型</p>}
          <div className="row">
            <Button
              variant="primary"
              disabled={busy || selected.length === 0}
              onClick={() => void importModels()}
            >
              导入选中（{selected.length}）
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => setDiscovered(null)}>
              关闭预览
            </Button>
          </div>
        </section>
      )}
      <Dialog
        open={editing !== null}
        onOpenChange={(value) => {
          if (!value) setEditing(null);
        }}
        title={editing === "new" ? "添加模型" : "编辑模型"}
        className="model-dialog"
      >
        {editing !== null && (
          <ModelEditor
            providerId={provider.id}
            initial={editing === "new" ? undefined : editing}
            onSaved={(model) => {
              onSaved(model);
              setEditing(null);
            }}
            onCancel={() => setEditing(null)}
          />
        )}
      </Dialog>
      <Dialog
        open={deleting !== null}
        onOpenChange={(value) => {
          if (!value && !busy) setDeleting(null);
        }}
        title="删除模型"
      >
        <p>确定删除「{deleting?.display_name || deleting?.model_id}」？</p>
        <Alert>{error}</Alert>
        <div className="dialog-actions">
          <Button disabled={busy} onClick={() => setDeleting(null)}>
            取消
          </Button>
          <Button disabled={busy} variant="danger" onClick={() => void deleteModel()}>
            确认删除
          </Button>
        </div>
      </Dialog>
    </section>
  );
}

function optionalPositive(value: string): number | null {
  if (!value.trim()) return null;
  return z.number().int().positive().parse(Number(value));
}

function ModelEditor({
  providerId,
  initial,
  onSaved,
  onCancel,
}: {
  providerId: string;
  initial?: Model;
  onSaved: (model: Model) => void;
  onCancel: () => void;
}) {
  const [modelId, setModelId] = useState(initial?.model_id ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [protocol, setProtocol] = useState(initial?.protocol_override ?? "inherit");
  const [context, setContext] = useState(initial?.context_window?.toString() ?? "");
  const [output, setOutput] = useState(initial?.max_output_tokens?.toString() ?? "");
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const saved = await request(
        initial ? `/models/${initial.id}` : `/providers/${providerId}/models`,
        modelSchema,
        {
          method: initial ? "PATCH" : "POST",
          body: {
            ...(initial ? {} : { model_id: modelId.trim() }),
            display_name: displayName.trim() || modelId.trim(),
            protocol_override: protocol === "inherit" ? null : protocolSchema.parse(protocol),
            context_window: optionalPositive(context),
            max_output_tokens: optionalPositive(output),
            enabled,
          },
        },
      );
      onSaved(saved);
    } catch (failure) {
      setError(
        failure instanceof z.ZodError
          ? "上下文与输出上限应为正整数，或留空继承默认值。"
          : errorText(failure),
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="model-form" onSubmit={(event) => void save(event)}>
      <div className="stack model-form-scroll">
        <Input
          label="模型 ID"
          value={modelId}
          disabled={!!initial}
          onChange={(event) => setModelId(event.target.value)}
          required
        />
        <Input
          label="显示名称"
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
        />
        <Select
          label="单模型协议覆盖"
          value={protocol}
          onChange={setProtocol}
          options={[{ value: "inherit", label: "继承提供商默认协议" }, ...protocols]}
        />
        <div className="settings-grid">
          <Input
            label="上下文窗口（可选）"
            type="number"
            min={1}
            step={1}
            value={context}
            onChange={(event) => setContext(event.target.value)}
          />
          <Input
            label="最大输出 tokens（可选）"
            type="number"
            min={1}
            step={1}
            value={output}
            onChange={(event) => setOutput(event.target.value)}
          />
        </div>
        <Select
          label="状态"
          value={enabled ? "on" : "off"}
          onChange={(value) => setEnabled(value === "on")}
          options={[
            { value: "on", label: "启用" },
            { value: "off", label: "停用" },
          ]}
        />
        <Alert>{error}</Alert>
      </div>
      <div className="dialog-actions model-form-footer">
        <Button disabled={busy} onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" variant="primary" disabled={busy}>
          {busy ? "保存中…" : "保存模型"}
        </Button>
      </div>
    </form>
  );
}

function RulesSection({
  settings,
  activeBook,
  books,
  onSettingsSaved,
  onBookSaved,
}: {
  settings: GlobalSettings;
  activeBook: Book | null;
  books: Book[];
  onSettingsSaved: (settings: GlobalSettings) => void;
  onBookSaved: () => void;
}) {
  const [globalRules, setGlobalRules] = useState(settings.global_rules_markdown);
  const [savingGlobal, setSavingGlobal] = useState(false);
  const [globalError, setGlobalError] = useState("");
  const [bookId, setBookId] = useState(activeBook?.id ?? books[0]?.id ?? "");
  const currentBook = books.find((item) => item.id === bookId) ?? activeBook ?? books[0] ?? null;
  const [bookRules, setBookRules] = useState(currentBook?.rules_markdown ?? "");
  const [savingBook, setSavingBook] = useState(false);
  const [bookError, setBookError] = useState("");

  useEffect(() => {
    if (!currentBook) return;
    setBookRules(currentBook.rules_markdown);
  }, [currentBook]);

  async function saveGlobalRules(event: FormEvent) {
    event.preventDefault();
    setSavingGlobal(true);
    setGlobalError("");
    try {
      const result = await request("/settings", settingsSchema, {
        method: "PATCH",
        body: {
          default_model_id: settings.default_model_id,
          background_model_id: settings.background_model_id,
          global_rules_markdown: globalRules,
        },
      });
      onSettingsSaved(result);
    } catch (failure) {
      setGlobalError(errorText(failure));
    } finally {
      setSavingGlobal(false);
    }
  }

  async function saveBookRules(event: FormEvent) {
    event.preventDefault();
    if (!currentBook) return;
    setSavingBook(true);
    setBookError("");
    try {
      await request(`/books/${currentBook.id}`, bookSchema, {
        method: "PATCH",
        body: { rules_markdown: bookRules },
      });
      onBookSaved();
    } catch (failure) {
      setBookError(errorText(failure));
    } finally {
      setSavingBook(false);
    }
  }

  return (
    <div className="stack">
      <form className="settings-card" onSubmit={(event) => void saveGlobalRules(event)}>
        <div className="settings-card-header">
          <div className="settings-card-title">
            <h3>全局写作规则</h3>
            <span className="settings-card-subtitle">
              对所有作品生效，拼接在模式系统提示词之后（Markdown）
            </span>
          </div>
          <Button type="submit" variant="primary" disabled={savingGlobal}>
            {savingGlobal ? "保存中…" : "保存全局规则"}
          </Button>
        </div>
        <Textarea
          label="全局规则内容"
          rows={8}
          value={globalRules}
          onChange={(event) => setGlobalRules(event.target.value)}
          placeholder="记录个人文风偏好、忌用表达或通用叙事约束…"
        />
        <Alert>{globalError}</Alert>
      </form>
      <form className="settings-card" onSubmit={(event) => void saveBookRules(event)}>
        <div className="settings-card-header">
          <div className="settings-card-title">
            <h3>单书写作规则</h3>
            <span className="settings-card-subtitle">
              仅对所选作品生效，拼接在全局规则与作品信息之后（Markdown）
            </span>
          </div>
          <div className="row">
            {books.length > 0 && currentBook && (
              <div className="rules-book-select">
                <Select
                  compact
                  label="选择作品"
                  value={currentBook.id}
                  onChange={(value) => {
                    setBookId(value);
                    const picked = books.find((item) => item.id === value);
                    if (picked) setBookRules(picked.rules_markdown);
                  }}
                  options={books.map((item) => ({ value: item.id, label: item.title }))}
                />
              </div>
            )}
            <Button type="submit" variant="primary" disabled={savingBook || !currentBook}>
              {savingBook ? "保存中…" : "保存单书规则"}
            </Button>
          </div>
        </div>
        {currentBook ? (
          <Textarea
            label="单书规则内容"
            rows={8}
            value={bookRules}
            onChange={(event) => setBookRules(event.target.value)}
            placeholder="记录本书专属的人称视角、时代口吻或特殊禁忌…"
          />
        ) : (
          <p className="muted">暂无作品，请先在书架新建作品。</p>
        )}
        <Alert>{bookError}</Alert>
      </form>
    </div>
  );
}

function PromptEditor({ initial, onSaved }: { initial: Prompt[]; onSaved: () => void }) {
  const [mode, setMode] = useState<"discuss" | "write" | "review">("discuss");
  const [prompts, setPrompts] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reset, setReset] = useState(false);
  const current = prompts.find((item) => item.mode === mode);
  const activeMeta = modeMeta.find((item) => item.value === mode) ?? modeMeta[0];

  async function save(restore: boolean) {
    if (!current) return;
    setBusy(true);
    setError("");
    try {
      const saved = await request(`/settings/prompts/${current.mode}`, promptSchema, {
        method: restore ? "DELETE" : "PUT",
        ...(restore ? {} : { body: { markdown: current.markdown } }),
      });
      setPrompts((items) => items.map((item) => (item.mode === saved.mode ? saved : item)));
      setReset(false);
      onSaved();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="prompts-master-detail"
      onSubmit={(event) => {
        event.preventDefault();
        void save(false);
      }}
    >
      <div className="prompts-mode-list" aria-label="写作模式">
        {modeMeta.map((item) => {
          const Icon = item.icon;
          return (
            <Button
              key={item.value}
              variant="ghost"
              aria-current={mode === item.value ? "true" : undefined}
              onClick={() => {
                setMode(item.value);
                setError("");
              }}
            >
              <Icon size={15} />
              {item.label}模式
            </Button>
          );
        })}
      </div>
      <div className="prompts-editor-pane">
        <div className="settings-card-header">
          <div className="settings-card-title">
            <h3>{activeMeta.label}模式提示词</h3>
            <span className="tool-scope-hint">{activeMeta.toolsHint}</span>
          </div>
          <div className="row">
            <Button disabled={busy || !current} onClick={() => setReset(true)}>
              <RotateCcw size={14} />
              恢复默认
            </Button>
            <Button type="submit" variant="primary" disabled={busy || !current}>
              {busy ? "保存中…" : "保存提示词"}
            </Button>
          </div>
        </div>
        {current && (
          <Textarea
            label="系统提示词"
            value={current.markdown}
            onChange={(event) =>
              setPrompts((items) =>
                items.map((item) =>
                  item.mode === mode ? { ...item, markdown: event.target.value } : item,
                ),
              )
            }
          />
        )}
        <Alert>{error}</Alert>
      </div>
      <Dialog
        open={reset}
        onOpenChange={(value) => {
          if (!busy) setReset(value);
        }}
        title="恢复默认提示词"
        description="将替换当前模式的自定义提示词，并立即保存。"
      >
        <Alert>{error}</Alert>
        <div className="dialog-actions">
          <Button disabled={busy} onClick={() => setReset(false)}>
            取消
          </Button>
          <Button variant="primary" disabled={busy} onClick={() => void save(true)}>
            确认恢复
          </Button>
        </div>
      </Dialog>
    </form>
  );
}
