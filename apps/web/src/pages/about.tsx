import { Link } from 'react-router';

import { consoleButtonClass } from '@/components/console/button-class';
import { siteUrl } from '@/lib/site-links';

export function AboutPage() {
  return (
    <section className="mx-auto max-w-3xl px-6 py-16">
      <p className="font-mono text-[12px] font-bold text-ha-accent-soft">
        关于
      </p>
      <h1 className="mt-2 text-[30px] font-bold tracking-[-0.01em] text-ha-text">
        关于 beauty moon
      </h1>

      <div className="mt-8 space-y-5 text-[14px] leading-7 text-ha-text-3">
        <p>
          beauty moon 是一个 AI 办公助手:把邮件、日程、文档、周报这类重复活 交给
          Agent,你只需要在关键的地方点个头。
        </p>
        <p>
          它的干活方式固定,不靠「许愿」:先给出计划 → 你放行才执行 →
          该用的工具自己调 → 结果自己验收,不合格就返工 → 最后交出成品。
          危险操作(比如发邮件)还会
          <span className="font-medium text-ha-text-body">再确认一次</span>
          ,不会先斩后奏。
        </p>
        <p>
          每一步都会写进事件流水:谁、什么时候、在哪个账号上、做了什么、结果如何。
          可以回放、可以追责——哪怕某一步失败了,失败的记录也会留在那里,
          不会被后来的「重试」抹掉。
        </p>
      </div>

      <h2 className="mt-14 text-[20px] font-semibold text-ha-text">
        我们相信的四件事
      </h2>
      <dl className="mt-3 divide-y divide-ha-border">
        {[
          {
            term: '先看后做',
            detail:
              '计划先给人看,放行才动手;只有危险操作单独确认。人审是默认,不是例外。',
          },
          {
            term: '全程留痕',
            detail:
              '每一步都进事件流水,可回放、可审计。跨服务的通知也一样:先落库再投递,事务回滚就不会发出去。',
          },
          {
            term: '诚实',
            detail:
              '失败就是失败,不静默吞错,也不把「没做」讲成「做了」;查不到就说查不到,取不到件就报错。',
          },
          {
            term: '边界清晰',
            detail:
              '模型只经统一网关调用,账号凭证只进不出;每个租户的数据彼此隔离,区域决定模型路由与数据存放位置。',
          },
        ].map((item) => (
          <div
            key={item.term}
            className="grid gap-2 py-5 sm:grid-cols-[7rem_1fr]"
          >
            <dt className="text-[15px] font-semibold text-ha-text-body">
              {item.term}
            </dt>
            <dd className="text-[13px] leading-6 text-ha-text-3">
              {item.detail}
            </dd>
          </div>
        ))}
      </dl>

      <div className="mt-12 flex flex-wrap gap-3">
        <a
          href={siteUrl('/')}
          className={consoleButtonClass({ variant: 'secondary', size: 'md' })}
        >
          返回产品官网
        </a>
        <Link
          to="/"
          className={consoleButtonClass({ variant: 'primary', size: 'md' })}
        >
          进入工作台
        </Link>
      </div>
    </section>
  );
}
