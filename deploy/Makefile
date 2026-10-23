.PHONY: test lint kind-up kind-down
test:        ## static + rendered checks for everything under deploy/
	python -m pytest deploy/tests -q
lint:
	docker run --rm -v $(PWD)/deploy/helm/cityforward:/c alpine/helm:3.17.0 lint /c -f /c/values-kind.yaml
	docker run --rm -v $(PWD)/deploy/helm/cityforward:/c alpine/helm:3.17.0 lint /c -f /c/values-aks.yaml
	terraform -chdir=deploy/terraform/azure fmt -check -recursive
kind-up:
	deploy/kind/bootstrap.sh
kind-down:
	deploy/kind/bootstrap.sh down
