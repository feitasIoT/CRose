package lib

import "sort"

func sortUsers(users []User) {
	sort.Slice(users, func(i, j int) bool {
		return users[i].Username < users[j].Username
	})
}
