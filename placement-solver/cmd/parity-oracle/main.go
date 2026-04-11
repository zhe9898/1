package main

import (
	"encoding/json"
	"flag"
	"log"
	"os"

	"zen70/placement-solver/internal/parityfixtures"
	"zen70/placement-solver/internal/solver"
)

type caseResult struct {
	Assignments   map[string]string `json:"assignments"`
	FeasiblePairs int32             `json:"feasible_pairs"`
	Result        string            `json:"result"`
}

func main() {
	fixturePath := flag.String("fixture", "", "Path to the shared placement parity fixture JSON file")
	flag.Parse()

	if *fixturePath == "" {
		log.Fatal("missing required -fixture")
	}

	expanded, err := parityfixtures.Expand(*fixturePath)
	if err != nil {
		log.Fatalf("load parity fixtures: %v", err)
	}

	results := make(map[string]caseResult, len(expanded))
	for _, testCase := range expanded {
		solveResult := solver.Solve(testCase.Request)
		results[testCase.Name] = caseResult{
			Assignments:   solveResult.Assignments,
			FeasiblePairs: solveResult.FeasiblePairs,
			Result:        solveResult.Result,
		}
	}

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(results); err != nil {
		log.Fatalf("encode oracle results: %v", err)
	}
}
