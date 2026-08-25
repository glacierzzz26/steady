package service

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"

	"quant-system/backend/internal/model"
	"quant-system/backend/internal/repository"
)

// factorManageDB G10 生命周期测试库（TEST_DB_DSN 门控，对齐 factorStatsDB）：
// 先删后建 factor_definition/factor_trial 等，保证可重复执行。
func factorManageDB(t *testing.T) *gorm.DB {
	t.Helper()
	dsn := os.Getenv("TEST_DB_DSN")
	if dsn == "" {
		t.Skip("TEST_DB_DSN 未设置，跳过集成测试（本地/CI 设置后自动启用）")
	}
	if strings.Contains(dsn, "dbname=quant_system") && !strings.Contains(dsn, "dbname=quant_system_test") {
		t.Fatal("拒绝连接生产库 quant_system，请使用独立测试库 quant_system_test")
	}
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{
		NamingStrategy: schema.NamingStrategy{SingularTable: true},
	})
	if err != nil {
		t.Fatalf("连接测试库失败: %v", err)
	}
	// 子表先删（FK 引用 factor_definition.name）
	if err := db.Migrator().DropTable(&model.FactorTrial{}, &model.FactorStat{}, &model.FactorValue{}); err != nil {
		t.Fatalf("清理 factor_trial/factor_stat/factor_value 失败: %v", err)
	}
	if err := db.Migrator().DropTable(&model.FactorDefinition{}); err != nil {
		t.Fatalf("清理 factor_definition 失败: %v", err)
	}
	if err := db.AutoMigrate(&model.FactorDefinition{}, &model.FactorTrial{},
		&model.FactorStat{}, &model.FactorValue{}); err != nil {
		t.Fatalf("AutoMigrate 失败: %v", err)
	}
	return db
}

func newFactorManageSvc(t *testing.T) (*FactorService, *gorm.DB) {
	t.Helper()
	db := factorManageDB(t)
	return NewFactorService(repository.NewFactorRepository(db)), db
}

func TestFactorLifecycle(t *testing.T) {
	svc, db := newFactorManageSvc(t)

	// 1. Create 草稿因子
	f, err := svc.Create(FactorInput{
		Name:     "ma_trend_ma10",
		Category: "trend",
		Formula:  "MA10 > MA20",
		Weight:   f64(0.2),
		Params:   json.RawMessage(`{"window":10}`),
	})
	if err != nil {
		t.Fatalf("Create: %v", err)
	}
	if f.Status != model.FactorStatusDraft || f.Version != "v1.0" {
		t.Fatalf("新建因子应为 draft/v1.0，got %s/%s", f.Status, f.Version)
	}

	// 2. 重名拒绝
	if _, err := svc.Create(FactorInput{Name: "ma_trend_ma10"}); err != ErrFactorExists {
		t.Fatalf("重名应返回 ErrFactorExists，got %v", err)
	}

	// 3. params 非对象拒绝
	if _, err := svc.Create(FactorInput{Name: "bad_params", Params: json.RawMessage(`[1,2]`)}); err == nil {
		t.Fatal("params 非 JSON 对象应拒绝")
	}

	// 4. Update 草稿可编辑
	f, err = svc.Update("ma_trend_ma10", FactorInput{Formula: "MA10 > MA20 且量能放大", Weight: f64(0.25)})
	if err != nil {
		t.Fatalf("Update draft: %v", err)
	}
	if f.Formula != "MA10 > MA20 且量能放大" || f.Weight != 0.25 {
		t.Fatalf("Update 未生效: %+v", f)
	}

	// 5. 状态机流转：draft→trial→verified→active→disabled
	for _, to := range []string{"trial", "verified", "active", "disabled"} {
		f, err = svc.Switch("ma_trend_ma10", to)
		if err != nil {
			t.Fatalf("Switch → %s: %v", to, err)
		}
		if f.Status != to {
			t.Fatalf("Switch → %s 后应为 %s，got %s", to, to, f.Status)
		}
	}

	// 6. disabled 可回 draft（回炉）或直接重新 active；active 不可编辑（仅草稿/停用可改）
	if _, err := svc.Switch("ma_trend_ma10", "active"); err != nil {
		t.Fatalf("disabled→active 重新上线应允许: %v", err)
	}
	if _, err := svc.Update("ma_trend_ma10", FactorInput{Formula: "X"}); err != ErrFactorNotEditable {
		t.Fatalf("active 编辑应拒绝，got %v", err)
	}
	if _, err := svc.Switch("ma_trend_ma10", "disabled"); err != nil {
		t.Fatalf("active→disabled: %v", err)
	}
	if _, err := svc.Update("ma_trend_ma10", FactorInput{Formula: "Y"}); err != nil {
		t.Fatalf("disabled 编辑应允许: %v", err)
	}
	if _, err := svc.Switch("ma_trend_ma10", "draft"); err != nil {
		t.Fatalf("disabled→draft 回炉应允许: %v", err)
	}
	if _, err := svc.Switch("ma_trend_ma10", "disabled"); err != nil {
		t.Fatalf("draft→disabled 放弃应允许: %v", err)
	}

	// 7. 非法流转拒绝（trial 状态不能直接 → active，须先 verified）
	if _, err := svc.Switch("ma_trend_ma10", "draft"); err != nil {
		t.Fatalf("disabled→draft: %v", err)
	}
	if _, err := svc.Switch("ma_trend_ma10", "trial"); err != nil {
		t.Fatalf("draft→trial 应允许: %v", err)
	}
	if _, err := svc.Switch("ma_trend_ma10", "active"); err != ErrFactorTransition {
		t.Fatalf("trial→active 非法流转应拒绝，got %v", err)
	}

	// 8. Fork：新名 _v2、版本提升、params 快照
	fork, err := svc.Fork("ma_trend_ma10")
	if err != nil {
		t.Fatalf("Fork: %v", err)
	}
	if fork.Name != "ma_trend_ma10_v2" || fork.Version != "v2.0" || fork.Status != model.FactorStatusDraft {
		t.Fatalf("Fork 元数据不符: %+v", fork)
	}
	if string(fork.Params) == "" || string(fork.Params) == "null" {
		t.Fatalf("Fork 应含 params 快照，got %s", fork.Params)
	}
	var p map[string]any
	if err := json.Unmarshal(fork.Params, &p); err != nil || p["window"] != float64(10) {
		t.Fatalf("Fork params 快照未复制: %s", fork.Params)
	}

	// 9. Delete：非草稿不可删（ma_trend_ma10 现为 trial）
	if err := svc.Delete("ma_trend_ma10"); err != ErrFactorNotDeletable {
		t.Fatalf("非草稿删除应拒绝，got %v", err)
	}

	// 10. Delete：草稿但有 factor_trial 引用 → 拒删
	if err := db.Create(&model.FactorTrial{FactorName: "ma_trend_ma10_v2", Status: "pending"}).Error; err != nil {
		t.Fatalf("seed factor_trial: %v", err)
	}
	if err := svc.Delete("ma_trend_ma10_v2"); err != ErrFactorHasRefs {
		t.Fatalf("有引用删除应拒绝，got %v", err)
	}

	// 11. 引用清理后草稿可删
	if err := db.Where("factor_name = ?", "ma_trend_ma10_v2").Delete(&model.FactorTrial{}).Error; err != nil {
		t.Fatalf("清理 factor_trial: %v", err)
	}
	if err := svc.Delete("ma_trend_ma10_v2"); err != nil {
		t.Fatalf("草稿无引用删除应成功: %v", err)
	}
	if _, err := svc.Get("ma_trend_ma10_v2"); err != ErrFactorNotFound {
		t.Fatalf("删除后应不存在，got %v", err)
	}

	// 12. List 含全部（含已存在的种子）
	items, err := svc.List()
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	found := false
	for _, it := range items {
		if it.Name == "ma_trend_ma10" {
			found = true
		}
	}
	if !found {
		t.Fatal("List 应包含 ma_trend_ma10")
	}
}
