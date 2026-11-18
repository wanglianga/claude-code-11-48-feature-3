import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'

const STATUS_LABEL = {
  draft: '草稿', in_review: '方法学审查中', approved: '已批准', rejected: '已拒绝',
}
const POOLING_PREMISE = {
  fixed: '固定效应为主：假设共同效应，更精确但会低估真实中心差异；随机效应作敏感性分析。',
  random: '随机效应为主：允许并吸收中心间差异，更保守；中心很少或精度极低时不稳定。',
}

export default function EffectPlanDetail() {
  const { id } = useParams()
  const { user } = useAuth()
  const [plan, setPlan] = useState(null)
  const [spec, setSpec] = useState(null)
  const [condsText, setCondsText] = useState('')
  const [variables, setVariables] = useState([])
  const [msg, setMsg] = useState(null)

  async function reload() {
    const p = await api.get(`/api/effect-plans/${id}`)
    setPlan(p)
    const current = p.versions.find((v) => v.id === p.current_version_id)
    const s = current ? JSON.parse(JSON.stringify(current.spec)) : null
    setSpec(s)
    setCondsText(JSON.stringify(s?.cohort_conditions || [], null, 2))
  }
  useEffect(() => {
    reload().catch((e) => setMsg({ type: 'bad', text: e.message }))
    api.get('/api/meta/dictionary').then((d) => setVariables(d.variables)).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  if (!plan || !spec) return <div className="panel">加载中…</div>
  const current = plan.versions.find((v) => v.id === plan.current_version_id)
  const editable = user.role === 'researcher' && current?.status === 'draft'
  const reviewable = user.role === 'reviewer' && current?.status === 'in_review'

  function set(k, v) { setSpec({ ...spec, [k]: v }) }

  function toggleCovariate(name, type) {
    const covs = spec.covariates.includes(name)
      ? spec.covariates.filter((c) => c !== name)
      : [...spec.covariates, name]
    const types = { ...spec.covariate_types }
    if (spec.covariates.includes(name)) delete types[name]
    else types[name] = type
    setSpec({ ...spec, covariates: covs, covariate_types: types })
  }

  async function save() {
    setMsg(null)
    let conds
    try {
      conds = JSON.parse(condsText)
    } catch {
      setMsg({ type: 'bad', text: '队列条件 JSON 无法解析' })
      return
    }
    try {
      const res = await api.put(`/api/effect-plans/${id}/save`, {
        base_version_id: plan.current_version_id,
        spec: { ...spec, cohort_conditions: conds },
      })
      setMsg({ type: 'ok', text: res.unchanged ? '规格未变化' : `已保存为 v${res.plan.versions[0].version_no}` })
      await reload()
    } catch (e) {
      setMsg({ type: 'bad', text: e.message })
    }
  }

  async function submit() {
    setMsg(null)
    try {
      await api.post(`/api/effect-plans/${id}/versions/${plan.current_version_id}/submit`)
      await reload()
    } catch (e) {
      setMsg({ type: 'bad', text: e.message })
    }
  }

  async function review(action) {
    const comment = window.prompt(action === 'approve' ? '方法学审批意见（可选）' : '拒绝原因')
    if (comment === null) return
    setMsg(null)
    try {
      await api.post(`/api/effect-plans/versions/${current.id}/${action}`, { comment })
      await reload()
    } catch (e) {
      setMsg({ type: 'bad', text: e.message })
    }
  }

  const catVars = variables.filter((v) => v.value_type === 'categorical')
  const outcomeVar = variables.find((v) => v.name === spec.outcome_variable)

  return (
    <>
      <div className="panel">
        <h2>研究方案 #{plan.id}：{plan.name}</h2>
        {msg && <div className={`alert ${msg.type === 'bad' ? 'bad' : 'ok'}`}>{msg.text}</div>}
        <p className="small muted">
          当前版本 v{current?.version_no} · 状态 <b>{STATUS_LABEL[current?.status]}</b>
          {current?.review_comment && ` · 审批意见：${current.review_comment}`}
        </p>

        <h3>队列规则（共同分析规则，JSON）</h3>
        <textarea rows={5} style={{ width: '100%' }} value={condsText}
          onChange={(e) => setCondsText(e.target.value)} disabled={!editable} />

        <div className="row" style={{ marginTop: 8 }}>
          <label className="field">治疗变量
            <select value={spec.treatment_variable} disabled={!editable}
              onChange={(e) => set('treatment_variable', e.target.value)}>
              {catVars.map((v) => <option key={v.name} value={v.name}>{v.label}（{v.name}）</option>)}
            </select>
          </label>
          <label className="field">治疗组取值
            <input value={spec.treated} disabled={!editable}
              onChange={(e) => set('treated', e.target.value)} />
          </label>
          <label className="field">对照组取值
            <input value={spec.control} disabled={!editable}
              onChange={(e) => set('control', e.target.value)} />
          </label>
          <label className="field">结局事件
            <select value={spec.outcome_value} disabled={!editable}
              onChange={(e) => set('outcome_value', e.target.value)}>
              {(outcomeVar?.allowed_values || []).map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
        </div>

        <h3>调整协变量（用于平衡摘要与共同支持检查）</h3>
        <div className="row">
          {variables.filter((v) => v.name !== spec.treatment_variable && v.name !== spec.outcome_variable && v.value_type !== 'date').map((v) => (
            <label key={v.name}>
              <input type="checkbox" disabled={!editable}
                checked={spec.covariates.includes(v.name)}
                onChange={() => toggleCovariate(v.name, v.value_type)} />{' '}
              {v.label}
            </label>
          ))}
        </div>

        <h3>汇总方法（运行前选择，报告将同时给出两种结果与前提）</h3>
        <div className="row">
          <label className="field">主效应量
            <select value={spec.primary_measure} disabled={!editable}
              onChange={(e) => set('primary_measure', e.target.value)}>
              <option value="risk_difference">缓解率差（RD）</option>
              <option value="log_odds_ratio_adjusted">协变量调整 log OR</option>
            </select>
          </label>
          <label className="field">主汇总方法
            <select value={spec.primary_pooling} disabled={!editable}
              onChange={(e) => set('primary_pooling', e.target.value)}>
              <option value="fixed">固定效应为主</option>
              <option value="random">随机效应为主</option>
            </select>
          </label>
        </div>
        <p className="small muted">{POOLING_PREMISE[spec.primary_pooling]}</p>

        {editable && (
          <div className="row" style={{ marginTop: 8 }}>
            <button className="secondary" onClick={save}>保存为新版本</button>
            <button onClick={submit}>提交方法学审查</button>
          </div>
        )}
        {reviewable && (
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={() => review('approve')}>批准（方法学）</button>
            <button className="danger" onClick={() => review('reject')}>拒绝</button>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>版本历史</h3>
        <table className="grid">
          <thead><tr><th>版本</th><th>状态</th><th>主方法</th><th>创建人</th><th>规格哈希</th><th>审批意见</th></tr></thead>
          <tbody>
            {plan.versions.map((v) => (
              <tr key={v.id}>
                <td>v{v.version_no}</td>
                <td><span className={`tag ${v.status === 'approved' ? 'ok' : v.status === 'rejected' ? 'bad' : 'warn'}`}>{STATUS_LABEL[v.status]}</span></td>
                <td>{v.spec.primary_pooling === 'random' ? '随机效应为主' : '固定效应为主'}</td>
                <td>{v.created_by}</td>
                <td className="mono small">{v.spec_hash.slice(0, 16)}…</td>
                <td className="small">{v.review_comment || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
