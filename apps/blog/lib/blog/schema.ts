import { z } from 'zod';

/** 案例项目的元信息。P0 只做展示,P2 才落地 labs 仓与校验脚本。 */
export const demoMetaSchema = z.object({
  repo: z.string().min(1).optional(),
  path: z.string().min(1).optional(),
  commit: z.string().min(1).optional(),
  language: z.enum(['typescript', 'python', 'mixed']).optional(),
  runtime: z.string().min(1).optional(),
  run: z.string().min(1).optional(),
  port: z.number().int().min(1).max(65535).optional(),
  status: z.enum(['idea', 'building', 'runnable', 'archived']).default('idea'),
  live: z.string().url().optional(),
});

export const postFrontmatterSchema = z.object({
  title: z.string().min(1).max(80),
  description: z.string().min(1).max(200),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, 'date 必须是 YYYY-MM-DD'),
  updated: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/, 'updated 必须是 YYYY-MM-DD')
    .optional(),
  tags: z
    .array(
      z
        .string()
        .min(1)
        .max(24)
        .regex(/^[^/\\]+$/, '标签不能包含斜杠'),
    )
    .min(1)
    .max(6),
  series: z
    .string()
    .min(1)
    .max(40)
    .regex(/^[^/\\]+$/, '系列名不能包含斜杠')
    .optional(),
  seriesOrder: z.number().int().positive().optional(),
  draft: z.boolean().default(false),
  lang: z.enum(['ts', 'python', 'mixed']).default('mixed'),
  demo: demoMetaSchema.optional(),
});

export type PostFrontmatter = z.infer<typeof postFrontmatterSchema>;
export type DemoMeta = z.infer<typeof demoMetaSchema>;
